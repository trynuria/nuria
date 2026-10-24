# Data and method references

The optical digit data are by E. Alpaydin and C. Kaynak (1998),
[Optical Recognition of Handwritten Digits](https://doi.org/10.24432/C50P49),
UCI Machine Learning Repository. UCI distributes this dataset under
[Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).
The original archive is retained without modification in `data/optdigits-original.zip`.
Training and evaluation normalize the existing 64 integer intensities by 16.

UCI reports different people in its training and test partitions. The inputs are
already segmented 8×8 digit summaries. This is a digit-classification benchmark,
not a demonstration of arbitrary document or photograph transcription. Writer
identifiers are not available in the compressed rows for a writer-cluster analysis.

The implementation uses conventional optimization and an explicit surrogate for
the hard firing threshold. See Neftci, Mostafa and Zenke,
[Surrogate Gradient Learning in Spiking Neural Networks](https://arxiv.org/abs/1901.09948).
This implementation is not a reproduction of that paper's experiments or a new
learning rule. Hard spikes determine the forward decisions; Adam and the declared
surrogate update the input and output weights during supervised training.

[Brian2 threshold and reset models](https://brian2.readthedocs.io/en/stable/introduction/brian1_to_2/neurongroup.html)
provide a separate reference execution for the declared discrete leaky
integrate-and-fire recurrence. That reference tests implementation agreement,
rather than validating biological realism or a computational advantage of spikes.
