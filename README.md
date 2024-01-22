# ComfyUI-DareMerge
Merge two checkpoint models by dare ties (https://github.com/yule-BUAA/MergeLM).  Now with CLIP support.

# Node List

## DareModelMerger

|category|node name|input type|output type|desc.|
| --- | --- | --- | --- | --- |
|unet|Model Merger (Masked)|`MODEL`, `MODEL`, `MODEL_MASK`|`MODEL`|Performs a masked block merge|
|unet|Model Merger (DARE)|`MODEL`, `MODEL`, `MODEL_MASK (optional)`|`MODEL`|Performs a DARE block merge|
|unet|MBW Merger (DARE)|`MODEL`, `MODEL`, `MODEL_MASK (optional)`|`MODEL`|Performs a DARE block merge, with full layer control (like MBW)|
|mask|Magnitude Masker|`MODEL`, `MODEL`|`MODEL_MASK`|Creates a mask based on the deltas of the parameters|
|clip|CLIP Merger (DARE)|`CLIP`, `CLIP`|`CLIP`|Performs a DARE merge on two CLIP|
|util|Normalize Model|`MODEL`, `MODEL`|`MODEL`|Normalizes one models parameter norm to another model|

### Merging
* In general, one means keep first model, zero means keep second model
* For DARE, we use the base model to determine which values to protect or include.
* For TIES, we can use the sum of the delta ties (as in the paper), or the count, or off to disable.
* Can accept a model mask, which will restrict changes to only modify the masked areas.

### Masks
* Larger thresholds on a mask means to reduce the amount of the second model more
* threshold_type is the way we determine where our threshold lies in our distribution since we use chunks, quantile will err towards the sparsity, median will use the chunk median.
* invert is whether we invert the threshold, so we keep the weights that are below the threshold instead of above.

## How to use
DARE-TIES does a stochastic selection of the parameters to keep, and then only performs updates in either the 'up' or 'down' direction.  According to the paper, the workflow should be as such:
* Take our model A, and build a magnitude mask based on a base model.
* Take model B, and merge it in to model A using the mask to protect model A's largest parameters.

*Of note, this merge method does use random sampling, so you should not just assume that your first random seed is the best one for your merge, and if it is not set to fixed that the merge will change every run.*

### Normalization
I am testing out a new normalization method, which is to normalize the norm of the parameters of one model to another.  This is done by taking the ratio of the norms, and then scaling the parameters of the first model by that ratio.  This is done in the `Normalize Model` node.  There are a few options, of most interest is the 'attn_only' option, which only scales Q and K relative to each other, and just that.  You should see no difference in the model's performance, but it might make the merge more stable.
