from __future__ import annotations

import functools
import os
import random
import warnings
from typing import Literal

import numpy as np
import torch
from pymatgen.core.structure import Structure
from torch.utils.data import DataLoader, Dataset
from torch.utils.data.sampler import SubsetRandomSampler

from chgnet import utils
from chgnet.graph import CrystalGraph, CrystalGraphConverter

warnings.filterwarnings("ignore")
datatype = torch.float32


class StructureData(Dataset):
    """A simple torch Dataset of structures."""

    def __init__(
        self,
        structures: list,
        energies: list,
        forces: list,
        stresses: list = None,
        magmoms: list = None,
        graph_converter: CrystalGraphConverter = None,
    ):
        """Initialize the dataset.

        Args:
            structures (list): a list of structures
            energies (list): a list of  energies
            forces (list): a list of forces
            stresses (List, optional): a list of stresses
            magmoms (List, optional): a list of magmoms
            graph_converter (CrystalGraphConverter, optional):
                a CrystalGraphConverter to convert the structures,
                if None, it will be set to CHGNet default converter
        """
        self.structures = structures
        self.energies = energies
        self.forces = forces
        self.stresses = stresses
        self.magmoms = magmoms
        self.keys = np.arange(len(structures))
        random.shuffle(self.keys)
        print(f"{len(self.structures)} structures imported")
        if graph_converter is not None:
            self.graph_converter = graph_converter
        else:
            self.graph_converter = CrystalGraphConverter(
                atom_graph_cutoff=5, bond_graph_cutoff=3
            )
        self.failed_idx = []
        self.failed_graph_id = {}

    def __len__(self):
        return len(self.keys)

    @functools.lru_cache(maxsize=None)  # Cache loaded structures
    def __getitem__(self, idx) -> (CrystalGraph, dict):
        """get one item in the dataset.

        Returns:
            crystal_graph (CrystalGraph): graph of the crystal structure
            targets (dict): list of targets. i.e. energy, force, stress
        """
        if idx not in self.failed_idx:
            graph_id = self.keys[idx]
            try:
                struc = Structure.from_dict(self.structures[graph_id])
                crystal_graph = self.graph_converter(
                    struc, graph_id=graph_id, mp_id=graph_id
                )
                targets = {
                    "e": torch.tensor(self.energies[graph_id], dtype=datatype),
                    "f": torch.tensor(self.forces[graph_id], dtype=datatype),
                }
                if self.stresses is not None:
                    # Convert VASP stress
                    targets["s"] = torch.tensor(
                        self.stresses[graph_id], dtype=datatype
                    ) * (-0.1)
                if self.magmoms is not None:
                    mag = self.magmoms[graph_id]
                    # use absolute value for magnetic moments
                    if mag is None:
                        targets["m"] = None
                    else:
                        targets["m"] = torch.abs(torch.tensor(mag, dtype=datatype))

                return crystal_graph, targets

            # Omit structures with isolated atoms. Return another random selected structure
            except:
                struc = Structure.from_dict(self.structures[graph_id])
                self.failed_graph_id[graph_id] = struc.composition.formula
                self.failed_idx.append(idx)
                idx = random.randint(0, len(self) - 1)
                return self.__getitem__(idx)
        else:
            idx = random.randint(0, len(self) - 1)
            return self.__getitem__(idx)


class CIFData(Dataset):
    """A dataset from cifs."""

    def __init__(
        self,
        cif_path: str,
        labels: str | dict = "labels.json",
        targets: Literal["ef", "efs", "efsm"] = "ef",
        graph_converter: CrystalGraphConverter = None,
        **kwargs,
    ):
        """Initialize the dataset from a directory containing cifs.

        Args:
            cif_path (str): path that contain all the graphs, labels.json
            labels (str, dict): the path or dictionary of labels
            targets ('ef' | 'efs' | 'efsm'): the training targets e=energy, f=forces, s=stress, m=magmons. Default = "ef"
            graph_converter (CrystalGraphConverter, optional):
                a CrystalGraphConverter to convert the structures,
                if None, it will be set to CHGNet default converter
            energy_str (str, optional): the key of energy in the labels.
                Default = "energy_per_atom".
            **kwargs: other arguments
        """
        self.data_dir = cif_path
        self.data = utils.read_json(os.path.join(cif_path, labels))
        self.cif_ids = list(self.data)
        random.shuffle(self.cif_ids)
        print(f"{cif_path}: {len(self.cif_ids)} structures imported")
        if graph_converter is not None:
            self.graph_converter = graph_converter
        else:
            self.graph_converter = CrystalGraphConverter(
                atom_graph_cutoff=5, bond_graph_cutoff=3
            )

        self.energy_str = kwargs.pop("energy_str", "energy_per_atom")
        self.targets = targets
        self.failed_idx = []
        self.failed_graph_id = {}

    def __len__(self):
        return len(self.cif_ids)

    @functools.lru_cache(maxsize=None)  # Cache loaded structures
    def __getitem__(self, idx):
        """Get one item in the dataset.

        Returns:
            crystal_graph (CrystalGraph): graph of the crystal structure
            targets (dict): list of targets. i.e. energy, force, stress
        """
        if idx not in self.failed_idx:
            try:
                graph_id = self.cif_ids[idx]
                mp_id = self.data[graph_id].get("material_id", graph_id)
                structure = Structure.from_file(
                    os.path.join(self.data_dir, f"{graph_id}.cif")
                )
                crystal_graph = self.graph_converter(
                    structure, graph_id=graph_id, mp_id=mp_id
                )
                targets = {}
                for i in self.targets:
                    if i == "e":
                        energy = self.data[graph_id][self.energy_str]
                        targets["e"] = torch.tensor(energy, dtype=datatype)
                    elif i == "f":
                        force = self.data[graph_id]["forces"]
                        targets["f"] = torch.tensor(force, dtype=datatype)
                    elif i == "s":
                        stress = self.data[graph_id]["stress"]
                        # Convert VASP stress
                        targets["s"] = torch.tensor(stress, dtype=datatype) * (-0.1)
                    elif i == "m":
                        mag = self.data[graph_id]["magmom"]
                        # use absolute value for magnetic moments
                        targets["m"] = torch.abs(torch.tensor(mag, dtype=datatype))
                return crystal_graph, targets

            # Omit structures with isolated atoms. Return another random selected structure
            except:
                try:
                    graph_id = self.cif_ids[idx]
                except:
                    print(idx, len(self.cif_ids))
                structure = Structure.from_file(
                    os.path.join(self.data_dir, f"{graph_id}.cif")
                )
                self.failed_graph_id[graph_id] = structure.composition.formula
                self.failed_idx.append(idx)
                idx = random.randint(0, len(self) - 1)
                return self.__getitem__(idx)
        else:
            idx = random.randint(0, len(self) - 1)
            return self.__getitem__(idx)


class GraphData(Dataset):
    """A dataset of graphs
    this is compatible with the graph.pt documents made by make_graphs.py
    we recommend you to use the dataset to avoid graph conversion steps.
    """

    def __init__(
        self,
        graph_path: str,
        labels: str | dict = "labels.json",
        targets: str = "efsm",
        exclude: str | list = None,
        **kwargs,
    ):
        """Initialize the dataset from a directory containing saved crystal graphs.

        Args:
            graph_path (str): path that contain all the graphs, labels.json
            labels (str, dict): the path or dictionary of labels
            targets (str): the training targets i.e. "ef", "efs", "efsm"
                Default = "efsm"
        """
        self.graph_path = graph_path
        if isinstance(labels, str):
            labels = os.path.join(graph_path, labels)
            print(f"Importing: {labels}")
            self.labels = utils.read_json(labels)
        elif isinstance(labels, dict):
            self.labels = labels

        if isinstance(exclude, str):
            self.excluded_graph = utils.read_json(exclude)
        elif isinstance(exclude, list):
            self.excluded_graph = exclude
        else:
            self.excluded_graph = []

        self.keys = []
        for mp_id, dic in self.labels.items():
            for graph_id, _ in dic.items():
                self.keys.append((mp_id, graph_id))
        random.shuffle(self.keys)
        print(f"{len(self.labels)} mp_ids, {len(self)} frames imported")
        if self.excluded_graph is not None:
            print(f"{len(self.excluded_graph)} graphs are pre-excluded")

        self.energy_str = kwargs.pop("energy_str", "energy_per_atom")
        self.targets = targets
        self.failed_idx = []
        self.failed_graph_id = []

    def __len__(self):
        return len(self.keys)

    def __getitem__(self, idx):
        """Get one item in the dataset.

        Returns:
            crystal_graph (CrystalGraph): graph of the crystal structure
            targets (dict): dictionary of targets. i.e. energy, force, stress, magmom
        """
        if idx not in self.failed_idx:
            mp_id, graph_id = self.keys[idx]
            if [mp_id, graph_id] in self.excluded_graph:
                self.failed_graph_id.append(graph_id)
                self.failed_idx.append(idx)
                idx = random.randint(0, len(self) - 1)
                return self.__getitem__(idx)
            try:
                graph_path = os.path.join(self.graph_path, f"{graph_id}.pt")
                crystal_graph = CrystalGraph.from_file(graph_path)
                targets = {}
                for i in self.targets:
                    if i == "e":
                        energy = self.labels[mp_id][graph_id][self.energy_str]
                        targets["e"] = torch.tensor(energy, dtype=datatype)
                    elif i == "f" or i == "force":
                        force = self.labels[mp_id][graph_id]["force"]
                        targets["f"] = torch.tensor(force, dtype=datatype)
                    elif i == "s" or i == "stresses":
                        stress = self.labels[mp_id][graph_id]["stress"]
                        # Convert VASP stress
                        targets["s"] = torch.tensor(stress, dtype=datatype) * (-0.1)
                    elif i == "m":
                        mag = self.labels[mp_id][graph_id]["magmom"]
                        # use absolute value for magnetic moments
                        if mag is None:
                            targets["m"] = None
                        else:
                            targets["m"] = torch.abs(torch.tensor(mag, dtype=datatype))
                return crystal_graph, targets

            # Omit failed structures. Return another random selected structure
            except:
                self.failed_graph_id.append(graph_id)
                self.failed_idx.append(idx)
                idx = random.randint(0, len(self) - 1)
                return self.__getitem__(idx)
        else:
            idx = random.randint(0, len(self) - 1)
            return self.__getitem__(idx)

    def get_train_val_test_loader(
        self,
        train_ratio: float = 0.8,
        val_ratio: float = 0.1,
        train_key: list[str] = None,
        val_key: list[str] = None,
        test_key: list[str] = None,
        batch_size=32,
        num_workers=0,
        pin_memory=True,
    ) -> (DataLoader, DataLoader, DataLoader):
        """partition the GraphData using materials id,
        randomly select the train_keys, val_keys, test_keys by train val test ratio,
        or use pre-defined train_keys, val_keys, and test_keys to create train, val, test loaders.

        Args:
            train_ratio (float): The ratio of the dataset to use for training
                Default = 0.8
            val_ratio (float): The ratio of the dataset to use for validation
                Default: 0.1
            train_key (List(str), optional): a list of mp_ids for train set
            val_key (List(str), optional): a list of mp_ids for val set
            test_key (List(str), optional): a list of mp_ids for test set
            batch_size (int): batch size
                Default = 32
            num_workers (int): The number of worker processes for loading the data
                see torch Dataloader documentation for more info
                Default = 0
            pin_memory (bool): Whether to pin the memory of the data loaders
                Default: True

        Returns:
            train_loader, val_loader, test_loader
        """
        train_labels, val_labels, test_labels = {}, {}, {}
        if train_key is None:
            mp_ids = list(self.labels)
            random.shuffle(mp_ids)
            n_train = int(train_ratio * len(mp_ids))
            n_val = int(val_ratio * len(mp_ids))
            train_key = mp_ids[:n_train]
            val_key = mp_ids[n_train : n_train + n_val]
            test_key = mp_ids[n_train + n_val :]
        for mp_id in train_key:
            try:
                train_labels[mp_id] = self.labels.pop(mp_id)
            except:
                continue
        train_dataset = GraphData(
            graph_path=self.graph_path,
            labels=train_labels,
            targets=self.targets,
            exclude=self.excluded_graph,
            energy_str=self.energy_str,
        )
        train_loader = DataLoader(
            train_dataset,
            batch_size=batch_size,
            collate_fn=collate_graphs,
            shuffle=True,
            num_workers=num_workers,
            pin_memory=pin_memory,
        )

        # Val
        for mp_id in val_key:
            try:
                val_labels[mp_id] = self.labels.pop(mp_id)
            except:
                continue
        val_dataset = GraphData(
            graph_path=self.graph_path,
            labels=val_labels,
            targets=self.targets,
            exclude=self.excluded_graph,
            energy_str=self.energy_str,
        )
        val_loader = DataLoader(
            val_dataset,
            batch_size=batch_size,
            collate_fn=collate_graphs,
            shuffle=True,
            num_workers=num_workers,
            pin_memory=pin_memory,
        )

        # Test
        if test_key is not None:
            for mp_id in test_key:
                try:
                    test_labels[mp_id] = self.labels.pop(mp_id)
                except:
                    continue
            test_dataset = GraphData(
                graph_path=self.graph_path,
                labels=test_labels,
                targets=self.targets,
                exclude=self.excluded_graph,
                energy_str=self.energy_str,
            )
            test_loader = DataLoader(
                test_dataset,
                batch_size=batch_size,
                collate_fn=collate_graphs,
                num_workers=num_workers,
                pin_memory=pin_memory,
            )
        else:
            test_loader = None
        return train_loader, val_loader, test_loader


class StructureJsonData(Dataset):
    """read structure and targets from a json file
    this function is used to load MPtrj dataset.
    """

    def __init__(
        self,
        data: str | dict,
        graph_converter: CrystalGraphConverter,
        targets: Literal["ef", "efs", "efsm"] = "efsm",
        **kwargs,
    ):
        """Initialize the dataset by reading Json files.

        Args:
            data (str | dict): json path or dir name that contain all the jsons
            graph_converter (CrystalGraphConverter): converter to convert pymatgen.core.Structure to graph
            targets ('ef' | 'efs' | 'efsm'): the training targets e=energy, f=forces, s=stress, m=magmons. Default = "efsm"
            **kwargs: other arguments
        """
        if isinstance(data, str):
            self.data = {}
            if os.path.isdir(data):
                for json_path in os.listdir(data):
                    if json_path.endswith(".json"):
                        print(f"Importing: {json_path}")
                        self.data.update(utils.read_json(os.path.join(data, json_path)))
            else:
                print(f"Importing: {data}")
                self.data.update(utils.read_json(data))
        elif isinstance(data, dict):
            self.data = data
        else:
            raise Exception("please provide a json path or dictionary")

        self.keys = []
        for mp_id, dic in self.data.items():
            for graph_id, _ in dic.items():
                self.keys.append((mp_id, graph_id))
        random.shuffle(self.keys)
        print(f"{len(self.data)} mp_ids, {len(self)} structures imported")
        self.graph_converter = graph_converter
        self.energy_str = kwargs.pop("energy_str", "energy_per_atom")
        self.targets = targets
        self.failed_idx = []
        self.failed_graph_id = {}

    def __len__(self):
        return len(self.keys)

    @functools.lru_cache(maxsize=None)  # Cache loaded structures
    def __getitem__(self, idx):
        """Get one item in the dataset.

        Returns:
            crystal_graph (CrystalGraph): graph of the crystal structure
            targets (dict): dictionary of targets. i.e. energy, force, stress, magmom
        """
        if idx not in self.failed_idx:
            mp_id, graph_id = self.keys[idx]
            try:
                struc = Structure.from_dict(self.data[mp_id][graph_id]["structure"])
                crystal_graph = self.graph_converter(
                    struc, graph_id=graph_id, mp_id=mp_id
                )

                targets = {}
                for i in self.targets:
                    if i == "e":
                        energy = self.data[mp_id][graph_id][self.energy_str]
                        targets["e"] = torch.tensor(energy, dtype=datatype)
                    elif i == "f" or i == "force":
                        force = self.data[mp_id][graph_id]["force"]
                        targets["f"] = torch.tensor(force, dtype=datatype)
                    elif i == "s" or i == "stresses":
                        stress = self.data[mp_id][graph_id]["stress"]
                        # Convert VASP stress
                        targets["s"] = torch.tensor(stress, dtype=datatype) * (-0.1)
                    elif i == "m":
                        mag = self.data[mp_id][graph_id]["magmom"]
                        # use absolute value for magnetic moments
                        if mag is None:
                            targets["m"] = None
                        else:
                            targets["m"] = torch.abs(torch.tensor(mag, dtype=datatype))
                return crystal_graph, targets

            # Omit structures with isolated atoms. Return another random selected structure
            except:
                structure = Structure.from_dict(self.data[mp_id][graph_id]["structure"])
                self.failed_graph_id[graph_id] = structure.composition.formula
                self.failed_idx.append(idx)
                idx = random.randint(0, len(self) - 1)
                return self.__getitem__(idx)
        else:
            idx = random.randint(0, len(self) - 1)
            return self.__getitem__(idx)

    def get_train_val_test_loader(
        self,
        train_ratio: float = 0.8,
        val_ratio: float = 0.1,
        train_key: list[str] = None,
        val_key: list[str] = None,
        test_key: list[str] = None,
        batch_size=32,
        num_workers=0,
        pin_memory=True,
    ) -> (DataLoader, DataLoader, DataLoader):
        """partition the Dataset using materials id,
        randomly select the train_keys, val_keys, test_keys by train val test ratio,
        or use pre-defined train_keys, val_keys, and test_keys to create train, val, test loaders.

        Args:
            train_ratio (float): The ratio of the dataset to use for training
                Default = 0.8
            val_ratio (float): The ratio of the dataset to use for validation
                Default: 0.1
            train_key (List(str), optional): a list of mp_ids for train set
            val_key (List(str), optional): a list of mp_ids for val set
            test_key (List(str), optional): a list of mp_ids for test set
            batch_size (int): batch size
                Default = 32
            num_workers (int): The number of worker processes for loading the data
                see torch Dataloader documentation for more info
                Default = 0
            pin_memory (bool): Whether to pin the memory of the data loaders
                Default: True

        Returns:
            train_loader, val_loader, test_loader
        """
        train_data, val_data, test_data = {}, {}, {}
        if train_key is None:
            mp_ids = list(self.data)
            random.shuffle(mp_ids)
            n_train = int(train_ratio * len(mp_ids))
            n_val = int(val_ratio * len(mp_ids))
            train_key = mp_ids[:n_train]
            val_key = mp_ids[n_train : n_train + n_val]
            test_key = mp_ids[n_train + n_val :]
        for mp_id in train_key:
            train_data[mp_id] = self.data.pop(mp_id)
        train_dataset = StructureJsonData(
            data=train_data,
            graph_converter=self.graph_converter,
            targets=self.targets,
            energy_str=self.energy_str,
        )
        train_loader = DataLoader(
            train_dataset,
            batch_size=batch_size,
            collate_fn=collate_graphs,
            shuffle=True,
            num_workers=num_workers,
            pin_memory=pin_memory,
        )
        for mp_id in val_key:
            val_data[mp_id] = self.data.pop(mp_id)
        val_dataset = StructureJsonData(
            data=val_data,
            graph_converter=self.graph_converter,
            targets=self.targets,
            energy_str=self.energy_str,
        )
        val_loader = DataLoader(
            val_dataset,
            batch_size=batch_size,
            collate_fn=collate_graphs,
            shuffle=True,
            num_workers=num_workers,
            pin_memory=pin_memory,
        )
        if test_key is not None:
            for mp_id in test_key:
                test_data[mp_id] = self.data.pop(mp_id)
            test_dataset = StructureJsonData(
                data=test_data,
                graph_converter=self.graph_converter,
                targets=self.targets,
                energy_str=self.energy_str,
            )
            test_loader = DataLoader(
                test_dataset,
                batch_size=batch_size,
                collate_fn=collate_graphs,
                num_workers=num_workers,
                pin_memory=pin_memory,
            )
        else:
            test_loader = None
        return train_loader, val_loader, test_loader


def collate_graphs(batch_data: list):
    """Collate of list of (graph, target) into batch data,.

    Args:
        batch_data (list): list of (graph, target(dict))

    Returns:
        graphs (List): a list of graphs
        targets (Dict): dictionary of targets, where key and values are:
            e (Tensor): energies of the structures [batch_size]
            f (Tensor): forces of the structures [n_batch_atoms, 3]
            s (Tensor): stresses of the structures [3*batch_size, 3]
            m (Tensor): magmom of the structures [n_batch_atoms]
    """
    graphs = []
    all_targets = {key: [] for key in batch_data[0][1]}
    for graph, targets in batch_data:
        graphs.append(graph)
        for target, value in targets.items():
            all_targets[target].append(value)
    if "e" in all_targets:
        all_targets["e"] = torch.tensor(all_targets["e"], dtype=datatype)
    return graphs, all_targets


def get_train_val_test_loader(
    dataset: Dataset,
    batch_size: int = 64,
    train_ratio: float = 0.8,
    val_ratio: float = 0.1,
    return_test: bool = True,
    num_workers: int = 0,
    pin_memory: bool = True,
):
    """Randomly partition a dataset into train, val, test loaders.

    Args:
        dataset (Dataset): The dataset to partition.
        batch_size (int): The batch size for the data loaders
            Default = 64
        train_ratio (float): The ratio of the dataset to use for training
            Default = 0.8
        val_ratio (float): The ratio of the dataset to use for validation
            Default: 0.1
        return_test (bool): Whether to return a test data loader
            Default = True
        num_workers (int): The number of worker processes for loading the data
            see torch Dataloader documentation for more info
            Default = 0
        pin_memory (bool): Whether to pin the memory of the data loaders
            Default: True

    Returns:
        train_loader, val_loader and optionally test_loader
    """
    total_size = len(dataset)
    indices = list(range(total_size))
    random.shuffle(indices)
    train_size = int(train_ratio * total_size)
    val_size = int(val_ratio * total_size)

    train_loader = DataLoader(
        dataset,
        batch_size=batch_size,
        collate_fn=collate_graphs,
        sampler=SubsetRandomSampler(indices=indices[0:train_size]),
        num_workers=num_workers,
        pin_memory=pin_memory,
    )
    val_loader = DataLoader(
        dataset,
        batch_size=batch_size,
        collate_fn=collate_graphs,
        sampler=SubsetRandomSampler(
            indices=indices[train_size : train_size + val_size]
        ),
        num_workers=num_workers,
        pin_memory=pin_memory,
    )
    if return_test:
        test_loader = DataLoader(
            dataset,
            batch_size=batch_size,
            collate_fn=collate_graphs,
            sampler=SubsetRandomSampler(indices=indices[train_size + val_size :]),
            num_workers=num_workers,
            pin_memory=pin_memory,
        )
        return train_loader, val_loader, test_loader
    else:
        return train_loader, val_loader


def get_loader(dataset, batch_size=64, num_workers=0, pin_memory=True):
    """Get a dataloader from a dataset.

    Args:
        dataset (Dataset): The dataset to partition.
        batch_size (int): The batch size for the data loaders
            Default = 64
        num_workers (int): The number of worker processes for loading the data
            see torch Dataloader documentation for more info
            Default = 0
        pin_memory (bool): Whether to pin the memory of the data loaders
            Default: True

    Returns:
        data_loader
    """
    data_loader = DataLoader(
        dataset,
        batch_size=batch_size,
        collate_fn=collate_graphs,
        shuffle=True,
        num_workers=num_workers,
        pin_memory=pin_memory,
    )

    return data_loader
