import math
from enum import IntEnum
from functools import lru_cache

import torch
from torch import nn
from torch.utils.data import Dataset, DataLoader
from tqdm import tqdm

from algorithms.building_block import BuildingBlock
from algorithms.decision_engines.nn.transformer import CustomTransformer
from algorithms.persistance import ModelCheckPoint
from dataloader.syscall import Syscall

DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')


class AnomalyScore(IntEnum):
    PRODUCT = 0
    MEAN = 1
    LAST = 2

    def __str__(self):
        return str(self.name)

    def __repr__(self):
        return self.__str__()


class Transformer(BuildingBlock):
    """ Decision engine based on the Transformer architecture."""
    VERSION = "00.20"

    def __init__(
            self,
            input_vector: BuildingBlock,
            distinct_syscalls: int,
            epochs: int,
            batch_size: int,
            anomaly_scoring: AnomalyScore,
            checkpoint: ModelCheckPoint,
            num_heads: int,
            layers: int,
            model_dim: int,
            dropout: float,
            feedforward_dim: int,
            pre_layer_norm: bool,
            language_model: bool,
            retrain=False):
        super().__init__()
        self._input_vector = input_vector
        self._dependency_list = [input_vector]
        self._distinct_syscalls = distinct_syscalls
        self._epochs = epochs
        self._batch_size = batch_size
        self._anomaly_scoring = anomaly_scoring
        self._checkpoint = checkpoint
        self._retrain = retrain
        self._language_model = language_model

        self.train_losses = {}
        self.val_losses = {}

        self._training_set = {
            'x': [],
            'y': []
        }
        self._validation_set = {
            'x': [],
            'y': []
        }

        # placeholder for start of sentence and end of sentence
        self._sos = distinct_syscalls + 1
        # distinct syscalls plus sos, plus 1 for unknown syscalls
        num_tokens = distinct_syscalls + 2
        self.transformer = TransformerModel(
            num_tokens,
            model_dim,
            num_heads,
            layers,
            layers,
            dropout,
            feedforward_dim,
            pre_layer_norm=pre_layer_norm,
            language_model=language_model
        ).to(DEVICE)

    def train_on(self, syscall: Syscall):
        self._append_call_split_to_set(syscall, self._training_set)

    def val_on(self, syscall: Syscall):
        self._append_call_split_to_set(syscall, self._validation_set)

    def _append_call_split_to_set(self, syscall: Syscall, container: dict):
        input_vector = self._input_vector.get_result(syscall)
        if input_vector is not None:
            x = list(input_vector[:-1])
            y = [self._sos] + list(input_vector[1:])
            if not self._language_model:
                x = [self._sos] + list(input_vector[:-1])
            container['x'].append(x)
            container['y'].append(y)

    def fit(self):
        loss_fn = nn.CrossEntropyLoss()

        learning_rate = 0.001
        optimizer = torch.optim.Adam(
            self.transformer.parameters(),
            lr=learning_rate,
            betas=(0.9, 0.98),
            eps=1e-9
        )

        t_dataset = TransformerDataset(self._training_set['x'], self._training_set['y'])
        t_dataset_val = TransformerDataset(self._validation_set['x'], self._validation_set['y'])

        train_dataloader = DataLoader(t_dataset, batch_size=self._batch_size, shuffle=False)
        val_dataloader = DataLoader(t_dataset_val, batch_size=self._batch_size, shuffle=False)
        last_epoch = 0
        if not self._retrain:
            last_epoch, self.train_losses, self.val_losses = self._checkpoint.load(
                self.transformer,
                optimizer,
                self._epochs
            )
        for epoch in tqdm(range(last_epoch + 1, self._epochs + 1)):
            # Training
            self.transformer.train()
            train_loss = 0
            for batch in train_dataloader:
                X, Y = batch
                y_input = Y[:, :-1]
                y_expected = Y[:, 1:]

                sequence_length = y_input.size(1)
                tgt_mask = self.transformer.get_tgt_mask(sequence_length).to(DEVICE)

                pred = self.transformer(X, y_input, tgt_mask)

                # Permute pred to have batch size first again
                pred = pred.permute(1, 2, 0)
                # prediction probability for every possible syscall
                loss = loss_fn(pred, y_expected)

                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

                train_loss += loss.detach().item()
            self.train_losses[epoch] = train_loss / len(train_dataloader)

            if epoch % 5 == 0:
                self.transformer.eval()
                val_loss = 0
                with torch.no_grad():
                    for batch in val_dataloader:
                        X, Y = batch
                        y_input = Y[:, :-1]
                        y_expected = Y[:, 1:]

                        # Get mask to mask out the next words
                        sequence_length = y_input.size(1)
                        tgt_mask = self.transformer.get_tgt_mask(sequence_length).to(DEVICE)

                        # Standard training except we pass in y_input and src_mask
                        pred = self.transformer(X, y_input, tgt_mask)

                        # Permute pred to have batch size first again
                        pred = pred.permute(1, 2, 0)
                        loss = loss_fn(pred, y_expected)
                        val_loss += loss.detach().item()
                self.val_losses[epoch] = val_loss / len(val_dataloader)
            self._checkpoint.save(self.transformer, optimizer, epoch, self.train_losses, self.val_losses)
        self.transformer.eval()

    def _calculate(self, syscall: Syscall):
        input_vector = self._input_vector.get_result(syscall)
        return self._cached_results(input_vector)

    @lru_cache(maxsize=5000)
    def _cached_results(self, input_vector):
        if input_vector is None:
            return None
        x_input = torch.tensor([[self._sos] + list(input_vector[:-1])], dtype=torch.long).to(device=DEVICE)
        y_input = torch.tensor([[self._sos] + list(input_vector[1:-1])], dtype=torch.long).to(device=DEVICE)
        if self._language_model:
            x_input = x_input[:, 1:]

        tgt_mask = self.transformer.get_tgt_mask(y_input.size(1)).to(DEVICE)
        pred = self.transformer(x_input, y_input, tgt_mask)
        predicted_probs = nn.Softmax(dim=2)(pred).squeeze(1)

        if self._anomaly_scoring == AnomalyScore.PRODUCT:
            conditional_prob = predicted_probs[range(predicted_probs.shape[0]), input_vector[1:]].prod()
        elif self._anomaly_scoring == AnomalyScore.MEAN:
            conditional_prob = predicted_probs[range(predicted_probs.shape[0]), input_vector[1:]].mean()
        elif self._anomaly_scoring == AnomalyScore.LAST:
            conditional_prob = predicted_probs[predicted_probs.shape[0] - 1, input_vector[-1]]
        else:
            raise NotImplementedError(f"Anomaly scoring strategy not implemented for {self._anomaly_scoring}")

        return 1 - float(conditional_prob)

    def depends_on(self) -> list:
        return self._dependency_list


class TransformerDataset(Dataset):

    def __init__(self, X, Y):
        self.X = torch.tensor(X, dtype=torch.long, device=DEVICE)
        self.Y = torch.tensor(Y, dtype=torch.long, device=DEVICE)

    def __len__(self):
        return len(self.X)

    def __getitem__(self, index):
        return self.X[index], self.Y[index]


class TransformerModel(nn.Module):
    """
    Model from "A detailed guide to Pytorch's nn.Transformer() module.", by
    Daniel Melchor: https://medium.com/@danielmelchor/a-detailed-guide-to-pytorchs-nn-transformer-module-c80afbc9ffb1
    """

    def __init__(
            self,
            num_tokens,
            dim_model,
            num_heads,
            num_encoder_layers,
            num_decoder_layers,
            dropout,
            feedforward_dim,
            language_model,
            pre_layer_norm):
        super().__init__()

        # INFO
        self.model_type = "Transformer"
        self.dim_model = dim_model
        self.language_model = language_model

        # LAYERS
        self.positional_encoder = PositionalEncoding(dim_model=dim_model, dropout_p=dropout, max_len=5000)
        self.embedding = nn.Embedding(num_tokens, dim_model)

        self.transformer = CustomTransformer(
            d_model=dim_model,
            nhead=num_heads,
            num_encoder_layers=num_encoder_layers,
            num_decoder_layers=num_decoder_layers,
            dropout=dropout,
            dim_feedforward=feedforward_dim,
            pre_layer_norm=pre_layer_norm,
            language_model=language_model
        )

        self.out = nn.Linear(dim_model, num_tokens)

    def forward(self, src, tgt, tgt_mask=None, src_pad_mask=None, tgt_pad_mask=None):
        # Src size must be (batch_size, src sequence length)
        # Tgt size must be (batch_size, tgt sequence length)

        # Embedding + positional encoding - Out size = (batch_size, sequence length, dim_model)
        src = self.embedding(src) * math.sqrt(self.dim_model)
        tgt = self.embedding(tgt) * math.sqrt(self.dim_model)
        src = self.positional_encoder(src)
        tgt = self.positional_encoder(tgt)

        # We could use the parameter batch_first=True, but our KDL version doesn't support it yet, so we permute
        # to obtain size (sequence length, batch_size, dim_model),
        src = src.permute(1, 0, 2)
        tgt = tgt.permute(1, 0, 2)

        # Transformer blocks - Out size = (sequence length, batch_size, num_tokens)
        transformer_out = self.transformer(
            src,
            tgt,
            tgt_mask=tgt_mask,
            src_key_padding_mask=src_pad_mask,
            tgt_key_padding_mask=tgt_pad_mask
        )
        out = self.out(transformer_out)

        return out

    def get_tgt_mask(self, size) -> torch.tensor:
        # Generates a square matrix where each row allows one word more to be seen
        mask = torch.tril(torch.ones(size, size) == 1)  # Lower triangular matrix
        mask = mask.float()
        mask = mask.masked_fill(mask == 0, float('-inf'))  # Convert zeros to -inf
        mask = mask.masked_fill(mask == 1, float(0.0))  # Convert ones to 0

        # EX for size=5:
        # [[0., -inf, -inf, -inf, -inf],
        #  [0.,   0., -inf, -inf, -inf],
        #  [0.,   0.,   0., -inf, -inf],
        #  [0.,   0.,   0.,   0., -inf],
        #  [0.,   0.,   0.,   0.,   0.]]

        return mask

    def create_pad_mask(self, matrix: torch.tensor, pad_token: int) -> torch.tensor:
        # If matrix = [1,2,3,0,0,0] where pad_token=0, the result mask is
        # [False, False, False, True, True, True]
        return matrix == pad_token


class PositionalEncoding(nn.Module):

    def __init__(self, dim_model, dropout_p, max_len):
        super().__init__()
        # Modified version from: https://pytorch.org/tutorials/beginner/transformer_tutorial.html
        # max_len determines how far the position can have an effect on a token (window)

        # Info
        self.dropout = nn.Dropout(dropout_p)

        # Encoding - From formula
        pos_encoding = torch.zeros(max_len, dim_model)
        positions_list = torch.arange(0, max_len, dtype=torch.float).view(-1, 1)  # 0, 1, 2, 3, 4, 5
        division_term = torch.exp(
            torch.arange(0, dim_model, 2).float() * (-math.log(10000.0)) / dim_model
        )  # 1000^(2i/dim_model)

        # PE(pos, 2i) = sin(pos/1000^(2i/dim_model))
        pos_encoding[:, 0::2] = torch.sin(positions_list * division_term)

        # PE(pos, 2i + 1) = cos(pos/1000^(2i/dim_model))
        pos_encoding[:, 1::2] = torch.cos(positions_list * division_term)

        # Saving buffer (same as parameter without gradients needed)
        pos_encoding = pos_encoding.unsqueeze(0).transpose(0, 1)
        self.register_buffer("pos_encoding", pos_encoding)

    def forward(self, token_embedding: torch.tensor) -> torch.tensor:
        # Residual connection + pos encoding
        return self.dropout(token_embedding + self.pos_encoding[:token_embedding.size(0), :])
