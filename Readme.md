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