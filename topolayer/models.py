import argparse
import math
import torch
import torch.nn as nn
import torch.nn.functional as F
import os

from topolayer.torch_persistent_homology.persistent_homology_cpu import compute_persistence_homology_batched_mt

import topolayer.coord_transforms as coord_transforms
import numpy as np


class TopologyLayer(torch.nn.Module):
    """Topological Aggregation Layer."""

    def __init__(self, features_in, features_out, num_filtrations,
                 num_coord_funs, filtration_hidden, num_coord_funs1=None,
                 dim1=False, residual_and_bn=False,
                 share_filtration_parameters=False, fake=False,
                 tanh_filtrations=False, swap_bn_order=False, dist_dim1=False):
        """
        num_coord_funs is a dictionary with the numbers of coordinate functions of each type.
        dim1 is a boolean. True if we have to return dim1 persistence.
        """
        super().__init__()

        self.dim1 = dim1

        self.features_in = features_in
        self.features_out = features_out

        self.num_filtrations = num_filtrations
        self.num_coord_funs = num_coord_funs

        self.filtration_hidden = filtration_hidden
        self.residual_and_bn = residual_and_bn
        self.share_filtration_parameters = share_filtration_parameters
        self.fake = fake
        self.swap_bn_order = swap_bn_order
        self.dist_dim1 = dist_dim1

        self.total_num_coord_funs = np.array(
            list(num_coord_funs.values())).sum()

        self.coord_fun_modules = torch.nn.ModuleList([
            getattr(coord_transforms, key)(output_dim=num_coord_funs[key])
            for key in num_coord_funs
        ])

        if self.dim1:
            assert num_coord_funs1 is not None
            self.coord_fun_modules1 = torch.nn.ModuleList([
                getattr(coord_transforms, key)(output_dim=num_coord_funs1[key])
                for key in num_coord_funs1
            ])

        final_filtration_activation = nn.Sigmoid() if tanh_filtrations else nn.Identity()
        if self.share_filtration_parameters:
            self.filtration_modules = torch.nn.Sequential(
                torch.nn.Linear(self.features_in, self.filtration_hidden),
                torch.nn.ReLU(),
                torch.nn.Linear(self.filtration_hidden, num_filtrations),
                final_filtration_activation
            )
        else:
            self.filtration_modules = torch.nn.ModuleList([
                torch.nn.Sequential(
                    torch.nn.Linear(self.features_in, self.filtration_hidden),
                    torch.nn.ReLU(),
                    torch.nn.Linear(self.filtration_hidden, 1),
                    final_filtration_activation
                ) for _ in range(num_filtrations)
            ])

        if self.residual_and_bn:
            in_out_dim = self.num_filtrations * self.total_num_coord_funs
            features_out = features_in
            self.bn = nn.BatchNorm1d(features_out)
            if self.dist_dim1 and self.dim1:
                self.out1 = torch.nn.Linear(self.num_filtrations * self.total_num_coord_funs, features_out)
        else:
            if self.dist_dim1:
                in_out_dim = self.features_in + 2 * self.num_filtrations * self.total_num_coord_funs
            else:
                in_out_dim = self.features_in + self.num_filtrations * self.total_num_coord_funs

        self.out = torch.nn.Linear(in_out_dim, features_out)


    def compute_persistence(self, x, batch, return_filtration = False):
        """
        Returns the persistence pairs as a list of tensors with shape [X.shape[0],2].
        The length of the list is the number of filtrations.
        """
        edge_index = batch.edge_index
        if self.share_filtration_parameters:
            filtered_v_ = self.filtration_modules(x)
        else:
            filtered_v_ = torch.cat([filtration_mod.forward(x)
                                     for filtration_mod in self.filtration_modules], 1)
        filtered_e_, _ = torch.max(torch.stack(
            (filtered_v_[edge_index[0]], filtered_v_[edge_index[1]])), axis=0)

        vertex_slices = torch.Tensor(batch._slice_dict['x']).long()
        edge_slices = torch.Tensor(batch._slice_dict['edge_index']).long()

        vertex_slices = vertex_slices.cpu()
        edge_slices = edge_slices.cpu()

        filtered_v_ = filtered_v_.cpu().transpose(1, 0).contiguous()
        filtered_e_ = filtered_e_.cpu().transpose(1, 0).contiguous()
        edge_index = edge_index.cpu().transpose(1, 0).contiguous()

        persistence0_new, persistence1_new = compute_persistence_homology_batched_mt(
            filtered_v_, filtered_e_, edge_index,
            vertex_slices, edge_slices)
        persistence0_new = persistence0_new.to(x.device)
        persistence1_new = persistence1_new.to(x.device)

        if return_filtration:
            return persistence0_new, persistence1_new, filtered_v_
        else:
            return persistence0_new, persistence1_new, None


    def compute_coord_fun(self, persistence, batch, dim1=False):
        """
        Input : persistence [N_points,2]
        Output : coord_fun mean-aggregated [self.num_coord_fun]
        """
        if dim1:
            coord_activation = torch.cat(
                [mod.forward(persistence) for mod in self.coord_fun_modules1], 1)
        else:
            coord_activation = torch.cat(
                [mod.forward(persistence) for mod in self.coord_fun_modules], 1)

        return coord_activation

    def compute_coord_activations(self, persistences, batch, dim1=False):
        """
        Return the coordinate functions activations pooled by graph.
        Output dims : list of length number of filtrations with elements : [N_graphs in batch, number fo coordinate functions]
        """

        coord_activations = [self.compute_coord_fun(
            persistence, batch=batch, dim1=dim1) for persistence in persistences]
        
  
        return torch.cat(coord_activations, 1)

    def collapse_dim1(self, activations, mask, slices):
        """
        Takes a flattened tensor of activations along with a mask and collapses it (sum) to have a graph-wise features

        Inputs : 
        * activations [N_edges,d]
        * mask [N_edge]
        * slices [N_graphs]
        Output:
        * collapsed activations [N_graphs,d]
        """
        collapsed_activations = []
        for el in range(len(slices)-1):
            activations_el_ = activations[slices[el]:slices[el+1]]
            mask_el = mask[slices[el]:slices[el+1]]

            # activations_el = activations_el_[mask_el].sum(axis=0)

            # use stat
            activations_el = activations_el_[mask_el]
            mean = activations_el.mean(axis=0)
            var = (activations_el * activations_el).mean(axis=0) - mean * mean
            activations_el = torch.cat((mean, var), dim=0)
            
            
            collapsed_activations.append(activations_el)

        return torch.stack(collapsed_activations)

    def forward(self, x, batch, return_filtration = False):
        persistences0, persistences1, filtration = self.compute_persistence(x, batch, return_filtration)
        
        if self.dim1:
            persistence1_mask = (persistences1 != 0).any(2).any(0)
            coord_activations1 = self.compute_coord_activations(
                persistences1, batch, dim1=True)
            graph_activations1 = self.collapse_dim1(coord_activations1, persistence1_mask, batch._slice_dict[
                "edge_index"])  # returns a vector for each graph
        else:
            graph_activations1 = None

        return (persistences0, persistences1), graph_activations1
    