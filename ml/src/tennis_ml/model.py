"""Clasificador sobre la secuencia de pose: GRU con pooling temporal. Pequeño (~150k parámetros), CPU."""

from __future__ import annotations

import torch
from torch import nn


class StrokeGRU(nn.Module):
    def __init__(self, features: int, classes: int, hidden: int = 128, layers: int = 2, dropout: float = 0.2) -> None:
        super().__init__()
        self.norm = nn.LayerNorm(features)
        self.gru = nn.GRU(features, hidden, num_layers=layers, batch_first=True,
                          dropout=dropout if layers > 1 else 0.0)
        self.head = nn.Sequential(nn.Dropout(dropout), nn.Linear(2 * hidden, classes))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: (batch, seq_len, features) → logits (batch, classes)."""
        out, _ = self.gru(self.norm(x))
        pooled = torch.cat([out.mean(dim=1), out[:, -1]], dim=1)  # media (forma global) + final (remate)
        return self.head(pooled)  # type: ignore[no-any-return]
