"""CPU stand-ins for the CUDA "pointops" that SymPoint-V2's network calls, so it runs
without a GPU. Same inputs, outputs and index conventions as the CUDA kernels: points
are batched by ``offset`` (cumulative sizes) and indices are global."""

import torch


def _segments(offset):
    start = 0
    for end in offset.tolist():
        yield start, int(end)
        start = int(end)


def _fps(xyz, m):
    """Furthest point sampling from the first point, as the CUDA kernel does."""
    idx = torch.zeros(m, dtype=torch.long)
    if xyz.shape[0] == 0 or m == 0:
        return idx
    dist = torch.full((xyz.shape[0],), 1e10)
    last = 0
    for i in range(m):
        idx[i] = last
        dist = torch.minimum(dist, ((xyz - xyz[last]) ** 2).sum(-1))
        last = int(torch.argmax(dist))
    return idx


def furthestsampling(xyz, offset, new_offset):
    out = [_fps(xyz[s:e].float().cpu(), ne - ns) + s for (s, e), (ns, ne) in zip(_segments(offset), _segments(new_offset))]
    return torch.cat(out).int().to(xyz.device)


def sectorized_fps(xyz, offset, new_offset, num_sectors, min_points=10000):
    out = []
    for (s, e), (ns, ne) in zip(_segments(offset), _segments(new_offset)):
        size, new_size = e - s, ne - ns
        sectors = 1 if size < min_points else num_sectors
        p = xyz[s:e].float().cpu()
        angle = torch.atan2(p[:, 0], p[:, 1])
        edges = torch.linspace(float(angle.min()), float(angle.max()) + 1e-4, sectors + 1)
        sizes = [new_size // sectors] * sectors
        sizes[-1] += new_size % sectors
        for k in range(sectors):
            members = torch.where((angle >= edges[k]) & (angle < edges[k + 1]))[0]
            out.append(members[_fps(p[members], min(sizes[k], len(members)))] + s)
    return torch.cat(out).int().to(xyz.device)


def knnquery(nsample, xyz, new_xyz, offset, new_offset):
    """The nsample nearest points in the same batch, nearest first: (idx, dist)."""
    if new_xyz is None:
        new_xyz = xyz
    idx_all, dist_all = [], []
    for (s, e), (ns, ne) in zip(_segments(offset), _segments(new_offset)):
        p, q = xyz[s:e].float(), new_xyz[ns:ne].float()
        k = min(nsample, p.shape[0])
        for c0 in range(0, q.shape[0], 4096):  # bounded memory
            d, i = torch.topk(torch.cdist(q[c0:c0 + 4096], p), k, dim=1, largest=False, sorted=True)
            if k < nsample:  # too few points: repeat the nearest
                i = torch.cat([i, i[:, :1].expand(-1, nsample - k)], 1)
                d = torch.cat([d, d[:, :1].expand(-1, nsample - k)], 1)
            idx_all.append(i + s)
            dist_all.append(d)
    return torch.cat(idx_all).int(), torch.cat(dist_all)


def queryandgroup(nsample, xyz, new_xyz, feat, idx, offset, new_offset, use_xyz=True):
    if new_xyz is None:
        new_xyz = xyz
    if idx is None:
        idx, _ = knnquery(nsample, xyz, new_xyz, offset, new_offset)
    m, c = new_xyz.shape[0], feat.shape[1]
    flat = idx.view(-1).long()
    grouped_feat = feat[flat, :].view(m, nsample, c)
    if not use_xyz:
        return grouped_feat
    grouped_xyz = xyz[flat, :].view(m, nsample, 3) - new_xyz.unsqueeze(1)
    return torch.cat((grouped_xyz, grouped_feat), -1), idx.long()


def interpolation(xyz, new_xyz, feat, offset, new_offset, k=3):
    idx, dist = knnquery(k, xyz, new_xyz, offset, new_offset)
    recip = 1.0 / (dist + 1e-8)
    weight = recip / torch.sum(recip, dim=1, keepdim=True)
    new_feat = torch.zeros(new_xyz.shape[0], feat.shape[1], dtype=feat.dtype, device=feat.device)
    for i in range(k):
        new_feat += feat[idx[:, i].long(), :] * weight[:, i].unsqueeze(-1)
    return new_feat


def grouping(input, idx):
    m, nsample = idx.shape
    return input[idx.view(-1).long()].view(m, nsample, input.shape[1])
