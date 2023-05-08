# Fast Python Dependency Resolution

This is a prototype for a fast dependency resolver for python.

The current state is that it is half rust ([pypi_types_crate](pypi_types_crate)) and half python ([resolve_prototype](resolve_prototype)), with pyo3 bindings in between. Currently only pypi is supported. The resolver is non-backtracking, pubgrub support is missing. It does resolve for multiple platforms and python versions, but it does not split disjoint requirements (the detection for disjoint markers exists though).

The resolver uses a tiered metadata retrieval system, from fast metadata (json api) to slow (building source distributions). We resolve as far as possible for each step before advancing to the next step. All queries for each step run in parallel.
1. Retrieve the list of versions
2. Retrieve the json metadata for a release
3. Retrieve the real METADATA file (https://github.com/pypi/warehouse/pull/13606 would allow skipping this). Consistency between wheel METADATA for different platforms is not yet ensured.
4. Build source distributions for the remaining cases

The test suite compares with pip (used by pip-compile, single platform only, test working) and poetry (multiplatform, can't produce poetry.lock yet), comparing with pdm is missing.

## Setup

```
poetry install
# For mac os, prepend RUSTFLAGS="-C link-arg=-undefined -C link-arg=dynamic_lookup" (https://github.com/PyO3/maturin/issues/1080)
maturin develop --release -m pypi_types_crate/Cargo.toml 
```

## Usage

```shell
python -m resolve_prototype.resolve <requirement>
python -m resolve_prototype.compare.compare_with_pip <requirement>
```

Requirements i test with

```text
# Nice easy but not trivial case
black[d,jupyter]
# A normal django project, some sdist-only
meine_stadt_transparent
# A huge ML tree
transformers[torch,sentencepiece,tokenizers,torch-speech,vision,integrations,timm,torch-vision,codecarbon,accelerate,video]
# relevant and slow with poetry
ibis-framework[all]
# another huge ML tree 
bio_embeddings[all]
```

## Testing

```shell
pytest
```

```shell
python -m resolve_prototype.compare.compare_all pip
python -m resolve_prototype.compare.compare_all poetry
```

## Profiling and benchmarking

```shell
python -m resolve_prototype.resolve_multiple black[d,jupyter] meine_stadt_transparent transformers[torch,sentencepiece,tokenizers,torch-speech,vision,integrations,timm,torch-vision,codecarbon,accelerate,video]
```

e.g.

```shell
py-spy record -o flamegraph.svg --native -- python -m resolve_prototype.resolve_multiple black[d,jupyter] meine_stadt_transparent ibis-framework[all] transformers[torch,sentencepiece,tokenizers,torch-speech,vision,integrations,timm,torch-vision,codecarbon,accelerate,video]
```

or 

```shell
hyperfine "python -m resolve_prototype.resolve_multiple black[d,jupyter] meine_stadt_transparent ibis-framework[all] transformers[torch,sentencepiece,tokenizers,torch-speech,vision,integrations,timm,torch-vision,codecarbon,accelerate,video]"
```