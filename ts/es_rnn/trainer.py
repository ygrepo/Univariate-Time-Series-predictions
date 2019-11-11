import copy

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

from ts.abstract_trainer import BaseTrainer
from ts.utils.loss_modules import np_sMAPE


class ESRNNTrainer(BaseTrainer):
    def __init__(self, model_name, model, dataloader, run_id, config, ohe_headers, csv_path, reload):
        super().__init__(model_name, model, dataloader, run_id, config, ohe_headers,
                         csv_path, reload)

    def train_batch(self, train, val, test, info_cat, idx):
        self.optimizer.zero_grad()
        network_pred, network_act, _, _, loss_mean_sq_log_diff_level = self.model(train, val,
                                                                                  test, info_cat,
                                                                                  idx)

        # Computing loss between predicted and truth training data deseasonalized and normalized.
        loss = self.criterion(network_pred, network_act)
        loss.backward()
        nn.utils.clip_grad_value_(self.model.parameters(), self.config["gradient_clipping"])
        self.optimizer.step()
        self.scheduler.step()
        return float(loss)

    def val(self, file_path):
        self.model.eval()
        with torch.no_grad():
            acts = []
            preds = []
            info_cats = []

            hold_out_loss = 0
            for batch_num, (train, val, test, info_cat, idx) in enumerate(self.data_loader):
                _, _, (hold_out_pred, network_output_non_train), \
                (hold_out_act, hold_out_act_deseas_norm), _ = self.model(train, val, test, info_cat, idx, testing=True)
                # Compute loss between normalized and deseasonalized predictions and
                # either validation or test data depending the value of the flag testing.
                hold_out_loss += self.criterion(network_output_non_train.unsqueeze(0).float(),
                                                hold_out_act_deseas_norm.unsqueeze(0).float())
                acts.extend(hold_out_act.view(-1).cpu().detach().numpy())
                preds.extend(hold_out_pred.view(-1).cpu().detach().numpy())
                info_cats.append(info_cat.cpu().detach().numpy())
            hold_out_loss = hold_out_loss / (batch_num + 1)

            info_cat_overall = np.concatenate(info_cats, axis=0)
            _hold_out_df = pd.DataFrame({"acts": acts, "preds": preds})
            cats = [val for val in self.ohe_headers[info_cat_overall.argmax(axis=1)] for _ in
                    range(self.config["output_size"])]
            _hold_out_df["category"] = cats

            overall_hold_out_df = copy.copy(_hold_out_df)
            overall_hold_out_df["category"] = ["Overall" for _ in cats]

            overall_hold_out_df = pd.concat((_hold_out_df, overall_hold_out_df))
            grouped_results = overall_hold_out_df.groupby(["category"]).apply(
                lambda x: np_sMAPE(x.preds, x.acts, x.shape[0]))

            results = grouped_results.to_dict()
            results["hold_out_loss"] = float(hold_out_loss.detach().cpu())
            print(results)

            self.log_values(results)
            grouped_path = file_path / ("grouped_results-{}.csv".format(self.epochs))
            grouped_results.to_csv(grouped_path, header=True)

        return hold_out_loss.detach().cpu().item()