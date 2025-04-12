## Prerequisites

- Python 3.13 or higher
- UV

## Running the Samples

Run one (or more) [agent](/samples/python/agents/README.md) A2A server and one of the [host applications](/samples/python/hosts/README.md). 

The following example will run the langgraph agent with the python CLI host:

1. Run an agent:
    ```bash
    uv run agents/langgraph
    ```
2. Run the example client
    ```
    uv run hosts/cli
    ```
---
**NOTE:** 
This is sample code and not production-quality libraries.
---
