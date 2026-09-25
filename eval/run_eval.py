"""
Evaluation runner — loads env, runs pipeline, saves results to eval/results/
Run from project root: python eval/run_eval.py
"""
import sys, os

# Load .env
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.evaluation.evaluator import run_evaluation
run_evaluation("config.yaml", rag_workers=8)
