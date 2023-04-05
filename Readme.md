# Fast Python Dependency Resolution

```
poetry install
maturin develop --release -m pypi_types_crate/Cargo.toml
```

```shell
python -m resolve_prototype.resolve [requirement]
python -m resolve_prototype.compare.compare_with_pip [requirement]
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