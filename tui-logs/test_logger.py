import time
import random
import sys
from logger import get_logger, trace, set_trace_id

# Initialize logger, appending to the sample.log for the TUI to pick up
logger = get_logger("my_app", log_file="sample.log", level="DEBUG")

@trace(log_args=True, log_result=True, logger_name="my_app")
def fetch_user_data(user_id: int):
    logger.info(f"Fetching data for user {user_id}")
    time.sleep(random.uniform(0.1, 0.5))
    if user_id < 0:
        raise ValueError("Invalid user ID")
    return {"id": user_id, "name": f"User_{user_id}", "status": "active"}

@trace(log_args=False, log_result=False, logger_name="my_app")
def process_data(data: dict):
    logger.debug(f"Processing {data['name']}")
    time.sleep(random.uniform(0.2, 0.8))
    # Simulate random warning
    if random.random() > 0.7:
        logger.warning(f"High memory usage during processing of {data['name']}", extra={"memory_mb": random.randint(500, 1500)})
    return True

def handle_request(user_id: int):
    # Set a trace ID for this entire request flow
    trace_id = set_trace_id()
    logger.info(f"Started new request", extra={"request_type": "user_sync"})
    
    try:
        data = fetch_user_data(user_id)
        process_data(data)
        logger.info("Request completed successfully")
    except Exception as e:
        logger.error("Request failed", exc_info=True)
        
if __name__ == "__main__":
    continuous = "--continuous" in sys.argv
    
    print(f"Generating sample logs with traces (Continuous: {continuous})...")
    
    try:
        i = 100
        while True:
            # Generate a request, 10% chance to fail
            user_id = i if random.random() > 0.1 else -1
            handle_request(user_id)
            i += 1
            
            if not continuous:
                if i > 103:
                    handle_request(-1) # Force an error before exiting
                    break
            else:
                time.sleep(random.uniform(0.5, 2.0))
                
    except KeyboardInterrupt:
        print("\nStopped.")
        
    print("Done! You can now run `python main.py sample.log` to view them in the TUI.")
