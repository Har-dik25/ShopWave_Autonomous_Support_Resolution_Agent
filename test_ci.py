import sys
from data_manager import get_all_tickets
from agent import resolve_ticket
from evaluator import evaluate_results
from concurrent.futures import ThreadPoolExecutor, as_completed

def main():
    print("Running CI/CD Evaluation Tests...")
    tickets = get_all_tickets()
    reports = [None] * len(tickets)
    
    # Run agent against all tickets
    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_idx = {
            executor.submit(resolve_ticket, t): idx
            for idx, t in enumerate(tickets)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            reports[idx] = future.result()
            
    # Evaluate
    evaluation = evaluate_results(tickets, reports)
    accuracy = evaluation.get("accuracy", 0)
    weighted_score = evaluation.get("weighted_score", 0)
    
    print(f"Overall Accuracy: {accuracy:.0%}")
    print(f"Weighted Score: {weighted_score:.0%}")
    
    # Assert minimum acceptable performance thresholds
    if accuracy < 0.8:
        print("❌ FAILED: Accuracy dropped below 80% threshold!")
        sys.exit(1)
        
    if weighted_score < 0.7:
        print("❌ FAILED: Weighted score dropped below 70% threshold!")
        sys.exit(1)
        
    print("✅ SUCCESS: Evaluation tests passed!")
    sys.exit(0)

if __name__ == "__main__":
    main()
