# Fast Python Dependency Resolution

Run `poetry install`. Install the Python packages for [pep508_rs](https://github.com/konstin/pep508_rs) and [pypi_types](pypi_types).

```shell
python -m resolve_prototype.resolve [requirement]
python -m resolve_prototype.compare_with_pip [requirement]
```

Requirements I test with

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

For profiling and benchmarking:

```text
python -m resolve_prototype.resolve_multiple black[d,jupyter] meine_stadt_transparent transformers[torch,sentencepiece,tokenizers,torch-speech,vision,integrations,timm,torch-vision,codecarbon,accelerate,video]
```

e.g.

```text
py-spy record -o flamegraph.svg -- python -m resolve_prototype.resolve_multiple black[d,jupyter] meine_stadt_transparent ibis[all] transformers[torch,sentencepiece,tokenizers,torch-speech,vision,integrations,timm,torch-vision,codecarbon,accelerate,video]
```
