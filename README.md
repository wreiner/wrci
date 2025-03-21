# WRCI

CI DSL for Model Driven Development  lecture at FHJ.

## DSL

### PIPELINE

```
PIPELINE(helper_image="debian:bookworm-slim", start_command="sleep infinity", name="compile-verify")
```

Every pipeline must be closed with the `END` keyword.

### Pipeline Variables

It is possible to define variables in a pipeline, they need to start with the `$` sign.

```
$arch = "armv7"
```

Every pipeline variable is added as an environment variable to `STEP`.

Special variables are the properties of the `PIPELINE` as well as the variable `LAST_RC` tracks the return codes of `STEP` commands.

### STEP

`STEP` will run the executable, most likely a script, placed under `.wrci/<pipeline_name>/<executable_name>`. It cannot run arbitrary commands directly.

```
STEP step-prepare.sh
```

Every `STEP` will collect its last return code in the variable `LAST_RC` which can be used in conditionals.
The pipeline will not stop if a `STEP` fails but will continue.

### MSG

`MSG` just prints a defined message. It is possible to include pipeline variables in the print output.

Example:

```
MSG("Validation failed with exit code $LAST_RC")
```

### Conditionals

Simple conditionals in the form of if/else are supported.

```
$arch = "armv7"
IF $arch == "armv7":
    MSG("$arch supported")
ELSE:
    MSG("$arch not supported")
END
```

`ELSE` is not mandatory, but every conditional must be closed with the `END` keyword.
Nested conditionals are supported but every conditional can only have a single statement.

### EXIT

`EXIT` will exit the pipeline at the point it is called, and will stop all running containers.

## Examples

For testing there are a set of pipelines which can be used as examples.
They can be found in the directory `tests/testpipelines`.
