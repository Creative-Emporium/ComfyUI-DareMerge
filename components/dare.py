# components/dare.py
from comfy.model_patcher import ModelPatcher
import torch
from typing import Dict, Tuple, Optional

from ..ddare.const import UNET_CATEGORY
from ..ddare.mask import ModelMask
from ..ddare.merge import merge_tensors, METHODS
from ..ddare.tensor import dare_ties_sparsification
from ..ddare.util import cuda_memory_profiler, get_device, get_patched_state


class DareUnetMerger:
    """
    A class to merge two diffusion U-Net models using calculated deltas, sparsification,
    and a weighted consensus method. This is the DARE-TIES method.
    
    https://arxiv.org/pdf/2311.03099.pdf
    """

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, tuple]:
        """
        Defines the input types for the merging process.

        Returns:
            Dict[str, tuple]: A dictionary specifying the required model types and parameters.
        """
        return {
            "required": {
                "model_a": ("MODEL",),
                "model_b": ("MODEL",),
                "drop_rate": ("FLOAT", {"default": 0.90, "min": 0.0, "max": 1.0, "step": 0.01}),
                "ties": (["sum", "count", "off"], {"default": "sum"}),
                "rescale": (["off", "on"], {"default": "off"}),
                "seed": ("INT", {"default": 1, "min":0, "max": 99999999999}),
                "method": (["comfy",] + METHODS, {"default": "comfy"}),
                "iterations": ("INT", {"default": 1, "min": 1, "max": 100, "step": 1}),
                "time": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "label": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "input": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "middle": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "output": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "out": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
            },
            "optional": {
                "model_mask": ("MODEL_MASK",),
            }
        }

    RETURN_TYPES = ("MODEL",)
    FUNCTION = "merge"
    CATEGORY = UNET_CATEGORY

    def merge(self, model_a: ModelPatcher, model_b: ModelPatcher, 
              method : str, seed : Optional[int] = None, clear_cache : bool = True,
              model_mask: Optional[ModelMask] = None, iterations : int = 1,
              **kwargs,) -> Tuple[ModelPatcher]:
        """
        Merges two ModelPatcher instances based on the weighted consensus of their parameters and sparsity.

        Args:
            model_a (ModelPatcher): The base model to be merged.
            model_b (ModelPatcher): The model to merge into the base model.
            method (str): The method to use for merging, either "lerp", "slerp", or "gradient".
            seed (int): The random seed to use for the merge.
            clear_cache (bool): Whether to clear the CUDA cache after each chunk. Default is False.
            iterations (int): The number of iterations to perform the merge.  Default is 1.
            model_mask (Optional[ModelMask]): The model mask to use for protection of our model_a. Default is None.
            **kwargs: Additional arguments specifying the merge ratios for different layers and sparsity.

        Returns:
            Tuple[ModelPatcher]: A tuple containing the merged ModelPatcher instance.
        """

        device = get_device()

        if seed is not None:
            torch.manual_seed(seed)
        
        if clear_cache and torch.cuda.is_available():
            torch.cuda.empty_cache()

        with cuda_memory_profiler():
            m = model_a.clone()  # Clone model_a to keep its structure
            model_a_sd = get_patched_state(m)
            model_b_sd = get_patched_state(model_b)

            # Merge each parameter from model_b into model_a
            for k in model_a_sd.keys():
                if k not in model_b_sd:
                    print("could not patch. key doesn't exist in model:", k)
                    continue

                ratio = self.calculate_layer_ratio(k, **kwargs)
                if ratio is None:
                    continue

                # Apply sparsification by the delta for this layer
                mask : torch.Tensor = model_mask.get_layer_mask(k) if model_mask is not None else None
                a : torch.Tensor = model_a_sd[k]
                b : torch.Tensor = model_b_sd[k]

                merged_a = a.clone()

                for i in range(iterations):
                    if seed is not None:
                        torch.manual_seed(seed + i)
                    sparsified_delta = dare_ties_sparsification(merged_a, b, device=device, **kwargs)
                    # If we have a mask, apply it to the delta, replacing true values with our delta
                    if mask is not None:
                        #print(f"Merging {k} with mask Included: {torch.count_nonzero(mask)}, Excluded: {torch.count_nonzero(~mask)}")
                        sparsified_delta = torch.where(mask.to(device), sparsified_delta.to(device), merged_a.to(device))

                    if method == "comfy":
                        merged_a = sparsified_delta

                        strength_patch = 1.0 - ratio
                        strength_model = ratio
                    else:
                        merged_a = merge_tensors(method, merged_a.to(device), sparsified_delta.to(device), 1 - ratio)

                        strength_model = 0
                        strength_patch = 1.0

                del sparsified_delta
                
                # Apply the sparsified delta as a patch
                m.add_patches({k: (merged_a.to('cpu'),)}, strength_patch, strength_model)

        if clear_cache and torch.cuda.is_available():
            torch.cuda.empty_cache()

        return (m,)

    @classmethod
    def scan_layer(cls, key : str, base : str, n : int, **kwargs):
        for i in range(n):
            my_key = f"{base}.{i}"
            if key.startswith(my_key):
                if my_key in kwargs:
                    return kwargs[my_key]
                else:
                    print(f"No weight for {my_key}")
                    return None
            elif i==(n-1):
                print(f"Unknown key: {key},i={i}")
                return None
        return None

    @classmethod
    def calculate_layer_ratio(cls, key, **kwargs) -> Optional[float]:
        k_unet = key[len("diffusion_model."):]
        ratio = None

        # Get our ratio for this layer
        if k_unet.startswith(f"input_blocks."):
            # use scan layer static
            ratio = kwargs.get("input", None)
        elif k_unet.startswith(f"middle_block."):
            ratio = kwargs.get("middle", None)
        elif k_unet.startswith(f"output_blocks."):
            ratio = kwargs.get("output", None)
        elif k_unet.startswith("out."):
            ratio = kwargs.get("out", None)
        elif k_unet.startswith("time"):
            ratio = kwargs.get("time", None)
        elif k_unet.startswith("label_emb"):
            ratio  = kwargs.get("label", None)
        else:
            print(f"Unknown key: {key}, skipping.")

        return ratio
