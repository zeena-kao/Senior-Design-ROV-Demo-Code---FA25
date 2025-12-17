#!/usr/bin/env python3
import RPi.GPIO as GPIO
import time
import requests
from requests.exceptions import ConnectionError, Timeout, HTTPError
import sys

# --------------------------


#SERVER_IP = "10.243.92.91" #Tufts Secure
SERVER_IP = "192.168.8.108" #Mini Router IP
SERVER_PORT = 5001

# The server is now asynchronous, meaning the response is immediate.
# We increase the timeout slightly to allow for network latency, but keep it low.
REQUEST_TIMEOUT = 1.0 

# --- Debouncing Constant ---
# Time (in seconds) to wait after a state change before accepting a new input.
DEBOUNCE_TIME = 0.02 

# --------------------------
# Switch Pin Mapping (BCM)
# Each motor has: (up_pin, down_pin)
# --------------------------
switch_pins = {
    "4": (2, 3),
    "2": (4, 17),
    "1": (27, 22), # Uncomment and verify pins for more motors
    "3": (10, 9),
}

# --- Initialization ---
try:
    GPIO.setmode(GPIO.BCM)
    for up_pin, down_pin in switch_pins.values():
        # Set switch pins as inputs with internal pull-up resistors
        GPIO.setup(up_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(down_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
except Exception as e:
    print(f"GPIO Initialization Error: {e}")
    sys.exit(1)


# --------------------------
# Helper: Send Command
# --------------------------
def send_motor_command(motor_num, action):
    """
    Sends: {"action": "forward"} or {"action": "stop"}.
    Handles specific network errors.
    """
    url = f"http://{SERVER_IP}:{SERVER_PORT}/motor/{motor_num}"
    try:
        # Use the slightly increased timeout
        resp = requests.post(url, json={"action": action}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status() # Raise exception for bad status codes (4xx or 5xx)
        
        # Server now returns 'command_accepted' immediately
        print(f"Motor {motor_num} → {action}: {resp.json().get('message', 'Success')}")
        
    except Timeout:
        # This is the error you were seeing, now better handled and less likely due to server change
        print(f"Motor {motor_num}: ERROR sending command: Request timed out after {REQUEST_TIMEOUT}s. (Server busy?)")
    except ConnectionError:
        print(f"Motor {motor_num}: CRITICAL ERROR: Could not connect to server at {SERVER_IP}:{SERVER_PORT}. Is it running?")
    except HTTPError as err:
        print(f"Motor {motor_num}: HTTP Error {err.response.status_code}. Response: {err.response.json()}")
    except Exception as e:
        print(f"Motor {motor_num}: Unknown Error sending command: {e}")

def send_shutdown_command():
    """Sends command to safely stop all motors and shut down the server process."""
    url = f"http://{SERVER_IP}:{SERVER_PORT}/shutdown"
    try:
        print("\nSending server shutdown command...")
        requests.post(url, timeout=REQUEST_TIMEOUT)
    except:
        # Ignore errors during shutdown command, as server may close connection instantly
        pass

# --------------------------
# Main Loop
# --------------------------
# Track last known states to avoid spamming server
last_states = {m: None for m in switch_pins.keys()}
# Track last time a command was accepted to implement debouncing
last_input_time = {m: 0.0 for m in switch_pins.keys()}


print(f"Monitoring {len(switch_pins)} motor switches → controlling motor server at {SERVER_IP}:{SERVER_PORT}.")
print("Press CTRL+C to stop.\n")

try:
    while True:
        current_time = time.time()
        for motor_num, (up_pin, down_pin) in switch_pins.items():

            up = GPIO.input(up_pin)
            down = GPIO.input(down_pin)

            # Determine switch state (Assuming a simple 3-position switch: UP, DOWN, or CENTER/NEUTRAL)
            if up == 0 and down == 1:
                state = "UP" # Signal UP (Forward)
            elif up == 1 and down == 0:
                state = "DOWN" # Signal DOWN (Reverse)
            else:
                state = "CENTER" # Neither/Both (Stop)

            # Check for state change AND debounce time elapsed
            if state != last_states[motor_num] and (current_time - last_input_time[motor_num] > DEBOUNCE_TIME):

                if state == "UP":
                    # UP means full forward speed
                    send_motor_command(motor_num, "forward")
                
                elif state == "DOWN":
                    # DOWN means full reverse speed
                    send_motor_command(motor_num, "reverse") # <--- NEW COMMAND

                else:
                    # CENTER (neutral position) maps to the 'stop' command.
                    send_motor_command(motor_num, "stop")

                # Update the state and the time stamp only after sending a command
                last_states[motor_num] = state
                last_input_time[motor_num] = current_time

        time.sleep(0.05)

except KeyboardInterrupt:
    print("\nClient interrupted.")
finally:
    # IMPORTANT: Shut down the server process too
    send_shutdown_command()
    GPIO.cleanup()
    print("Client application finished and GPIO cleaned up.")
