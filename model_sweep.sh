#!/bin/bash

# Define your list of model names here
NORMAL_MODELS=(
    "anthropic/claude-sonnet-5"
    "anthropic/claude-opus-5"
    "openrouter/deepseek/deepseek-v4-pro"
    "openrouter/deepseek/deepseek-chat"
    "openrouter/z-ai/glm-5.2"
    "openrouter/moonshotai/kimi-k3"
    "openrouter/gpt-oss-120b"
)
REASONING_MODELS=(
    "openai/gpt-5.6-luna"
    "openai/gpt-5.6-sol"
    "openai/gpt-5.6-terra"
)
REASONING_EFFORT=(
    "high"
    "medium"
    "low"
)
# Loop through each model in the array
for model in "${NORMAL_MODELS[@]}"; do
    echo "=========================================="
    echo "Starting extraction for: $model"
    echo "=========================================="
    
    # Run the extraction command
    uv run perla-extract extract ./downloads/ --model_name "$model"
    
    # Optional: check if the command succeeded
    if [ $? -eq 0 ]; then
        echo "✅ Successfully extracted $model"
    else
        echo "❌ Failed to extract $model"
    fi
    echo ""
done

for model in "${REASONING_MODELS[@]}"; do
    for effort in "${REASONING_EFFORT[@]}"; do
        echo "=========================================="
        echo "Starting extraction for: $model with reasoning effort: $effort"
        echo "=========================================="
        CMD_ARGS=(
                "extract" 
                "./downloads/" 
                "--model_name" "$model" 
                "--additional_params" "{'reasoning_effort':'$effort'}"
            )
        # Run the extraction command with reasoning effort
        uv run perla-extract "${CMD_ARGS[@]}"

        # Optional: check if the command succeeded
        if [ $? -eq 0 ]; then
            echo "✅ Successfully extracted $model with reasoning effort: $effort"
        else
            echo "❌ Failed to extract $model with reasoning effort: $effort"
        fi
        echo ""
    done
done
echo "All extractions complete!"