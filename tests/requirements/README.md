# CI requirements, hash-pinned

`pip install --require-hashes -r` installs exactly these files or fails, so a replaced package on the
index cannot reach CI.

- `engine.txt`: Pillow for the engine tests (Python 3.13, as the add-on image).
- `validate.txt`: the Supervisor's pins for `tests/validate_config.py` and PyYAML for
  `tests/docs_yaml_test.py` (Python 3.14), with their dependencies.

To update, change the pins and regenerate with [uv](https://docs.astral.sh/uv/):

```sh
printf 'pillow==11.3.0\n' > /tmp/engine.in
uv pip compile /tmp/engine.in --generate-hashes --python-version 3.13 \
  --python-platform x86_64-manylinux_2_28 --no-header -o tests/requirements/engine.txt
printf 'voluptuous==0.16.0\nawesomeversion==25.8.0\naiohttp==3.14.5\nPyYAML==6.0.3\n' > /tmp/validate.in
uv pip compile /tmp/validate.in --generate-hashes --python-version 3.14 \
  --python-platform x86_64-manylinux_2_28 --no-header -o tests/requirements/validate.txt
```
