import torch
import torch.nn.functional as F
from utils import graph_permutation, get_persistence_homology_distance


def train(model, optimizer, loader, linkpred, device, epoch):
    model.train()
    total_ce_loss, total_lp_loss, total_e_loss = 0, 0, 0
    total_ph_loss = 0
    vis = 0
    topo_sim_loss = 0
    
    for data in loader:
        optimizer.zero_grad()
        data.to(device)
        out, lp_loss, entropy_loss, ph_loss, vis_data = model(data, vis=True)
        out = F.log_softmax(out, dim=-1)
        loss = F.nll_loss(out, data.y.view(-1), reduction='mean')
        if linkpred and model.pooling_type == 'gnn':
            (lp_loss * 0 + loss + entropy_loss * 1 + ph_loss*0.0001).backward()
        else:
            loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 2.0)
        optimizer.step()
        total_ce_loss += loss * data.num_graphs
        total_lp_loss += lp_loss * data.num_graphs
        total_e_loss += entropy_loss * data.num_graphs
        total_ph_loss += ph_loss * data.num_graphs

        vis = 0
        
        # topological similarity
        # ori_phs, new_phs, ori_adj, adj, s = vis_data[0]
        # topo_sim_loss += get_persistence_homology_distance(ori_adj.detach(), adj.detach())
        
        
    return total_ce_loss / len(loader.dataset), total_lp_loss / len(loader.dataset), total_e_loss / len(loader.dataset)


@torch.no_grad()
def evaluate(model, loader, device, perm=False, evaluator=None, vis=None):
    model.eval()
    loss, lp_loss, e_loss, correct = 0, 0, 0, 0
    ph_loss = 0
    y_pred, y_true = [], []

    return_vis_data = None
    for data in loader:
        if perm:
            data = graph_permutation(data)
        data.to(device)
        out, lp, e, ph, vis_data = model(data, vis)
        if vis:
            return_vis_data = vis_data
        vis = False

        y_pred.append(out[:, 1])
        y_true.append(data.y)

        out = F.log_softmax(out, dim=-1)
        pred = out.max(1)[1]
        correct += pred.eq(data.y.view(-1)).sum().item()
        loss += F.nll_loss(out, data.y.view(-1), reduction='mean').item()* data.num_graphs
        lp_loss += lp * data.num_graphs
        e_loss += e * data.num_graphs
        ph_loss += ph * data.num_graphs

    y_pred = torch.cat(y_pred, dim=0)
    y_true = torch.cat(y_true, dim=0)

    if evaluator is None:
        acc = correct/len(loader.dataset)
    else:
        acc = evaluator.eval({'y_pred': y_pred.view(y_true.shape), 'y_true': y_true})[evaluator.eval_metric]
    return acc, loss / len(loader.dataset), lp_loss / len(loader.dataset), e_loss / len(loader.dataset), return_vis_data


def train_regression(model, optimizer, loader, linkpred, device):
    model.train()
    total_ce_loss, total_lp_loss, total_e_loss = 0, 0, 0
    for data in loader:
        optimizer.zero_grad()
        data.to(device)
        out, lp, e = model(data)
        loss = F.l1_loss(out, data.y.unsqueeze(1), reduction="mean")
        if linkpred and model.pooling_type == 'gnn':
            (lp + loss + e).backward()
        else:
            loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 2.0)
        optimizer.step()
        total_ce_loss += loss.item() * data.num_graphs
        total_lp_loss += lp.item() * data.num_graphs
        total_e_loss += e.item() * data.num_graphs
    return total_ce_loss / len(loader.dataset), total_lp_loss / len(loader.dataset), total_e_loss / len(loader.dataset)


@torch.no_grad()
def evaluate_regression(model, loader, device, perm=False, evaluator=None):
    model.eval()
    loss, lp_loss, e_loss, mae = 0, 0, 0, 0
    for data in loader:
        if perm:
            data = graph_permutation(data)
        data.to(device)
        out, lp, e = model(data)
        loss += F.l1_loss(out, data.y.unsqueeze(1), reduction="mean")*data.num_graphs
        lp_loss += lp.item() * data.num_graphs
        e_loss += e.item() * data.num_graphs
    return -loss / len(loader.dataset), loss / len(loader.dataset), \
           lp_loss / len(loader.dataset), e_loss / len(loader.dataset)

