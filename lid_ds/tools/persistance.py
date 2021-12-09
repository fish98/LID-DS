import os.path
import json

def save_to_json(result_dict: dict, path: str):
    """
        convert result dict to correct format
        cut known results and put the rest into config dict
    """
    results = {
        "true_positives": int,
        "false_positives": int,
        "true_negatives": int,
        "false_negatives": int,
        "alarm_count": int,
        "detection_rate": int,
        "consecutive_false_positives_normal": int,
        "consecutive_false_positives_exploits": int,
        "recall": int,
        "precision_with_cfa": int,
        "precision_with_syscalls": int
    }
    if os.path.exists(path):
        with open(path, 'r') as file:
            result_list = json.load(file)
        result_list.append(result_dict)
        with open(path, 'w') as file:
            json.dump(result_list, file)
    else:
        print('No persistent data yet')
        result_list = [result_dict]
        with open(path, 'w') as file:
            json.dump(result_list, file)

def load_from_json(path: str):
    try:
        with open(path, 'r') as file:
            result_list = json.load(file)
    except IOError:
        print(f'No results at {path}')
        result_list = None
    return result_list
