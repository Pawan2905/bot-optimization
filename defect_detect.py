import json
import os
import glob
import argparse

def analyze_bot_latency(directory_path):
    """
    Parses AWS Bedrock bot JSON logs in the given directory path to identify latency bottlenecks.
    """
    # Find all JSON files in the specified directory path
    search_pattern = os.path.join(directory_path, "*.json")
    json_files = glob.glob(search_pattern)
    
    if not json_files:
        print(f"No JSON files found in the directory: {directory_path}")
        return

    summary_data = []

    for file_path in json_files:
        file_name = os.path.basename(file_path)
        use_case = file_name.replace(".json", "").replace("_", " ").title()
        
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                interactions = json.load(f)
            except json.JSONDecodeError:
                print(f"Error reading {file_name}. Skipping...")
                continue
                
            for interaction in interactions:
                total_latency = interaction.get('latency', 0.0)
                max_step_time_ms = 0
                max_step_type = "Unknown"
                
                outputs = interaction.get('output', [])
                
                for step in outputs:
                    step_type = step.get('type', 'unknown_step')
                    
                    def find_max_time_in_node(node):
                        max_time = 0
                        if isinstance(node, dict):
                            for time_key in ['totalTimeMs', 'operationTotalTimeMs', 'durationMs']:
                                if time_key in node and isinstance(node[time_key], (int, float)):
                                    max_time = max(max_time, node[time_key])
                            for key, value in node.items():
                                max_time = max(max_time, find_max_time_in_node(value))
                        elif isinstance(node, list):
                            for item in node:
                                max_time = max(max_time, find_max_time_in_node(item))
                        return max_time

                    step_time_ms = find_max_time_in_node(step)
                    
                    if step_time_ms > max_step_time_ms:
                        max_step_time_ms = step_time_ms
                        max_step_type = step_type
                
                max_step_time_s = max_step_time_ms / 1000.0
                
                summary_data.append({
                    "Use Case": use_case,
                    "Total Latency (s)": round(total_latency, 2),
                    "Bottleneck Step": max_step_type,
                    "Step Delay (s)": round(max_step_time_s, 2)
                })

    # Print the results
    print(f"\nAnalyzing data in: {directory_path}")
    print("=" * 85)
    print(f"{'Use Case':<25} | {'Total Latency':<15} | {'Bottleneck Delay':<18} | {'Bottleneck Step Type'}")
    print("-" * 85)
    
    summary_data_sorted = sorted(summary_data, key=lambda x: x['Total Latency (s)'], reverse=True)
    
    for row in summary_data_sorted:
        print(f"{row['Use Case']:<25} | {row['Total Latency (s)']:<12} s | {row['Step Delay (s)']:<15} s | {row['Bottleneck Step']}")

if __name__ == "__main__":
    # Setup argument parser for the path
    parser = argparse.ArgumentParser(description="Analyze AWS Bedrock bot latency from JSON files.")
    parser.add_argument(
        "path", 
        type=str, 
        nargs="?", 
        default=".", 
        help="The folder path containing the JSON files (default: current directory)"
    )
    
    args = parser.parse_args()
    analyze_bot_latency(args.path)