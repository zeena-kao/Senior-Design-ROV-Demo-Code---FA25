#this is the motor server
#!/usr/bin/env python3
from flask import Flask, request, jsonify
from board import SCL, SDA
import busio
from adafruit_pca9685 import PCA9685
import atexit
import signal
import sys
import time
import os
import threading # Import threading module
import logging

# Set up logging to avoid Flask/Werkzeug noise
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# --------------------------
# PCA9685 Setup
# --------------------------
# I2C Address for the PCA9685 board. The default address is 0x40.
# If you are using multiple PCA9685 boards (like a Waveshare hat expansion)
# or if the board jumpers (A0-A5) are modified, this address must be updated.
PCA9685_ADDRESS = 0x40 

# NOTE on I2C: The I2C pins are correctly initialized using the board module's 
# SCL and SDA constants. If I2C communication fails, please check the physical 
# wiring, including I2C pull-up resistors and the PCA9685's address jumpers.
try:
    # Initialize I2C and PCA9685
    i2c = busio.I2C(SCL, SDA)
    # Explicitly pass the I2C address for configurability
    pca = PCA9685(i2c, address=PCA9685_ADDRESS) 
    pca.frequency = 50 # Standard 50Hz for servo/ESC control
    print("PCA9685 Initialized successfully.")
except Exception as e:
    # Set to None if initialization fails to run in simulation mode
    print(f"Hardware Error: Could not initialize PCA9685 (Address: {PCA9685_ADDRESS}): {e}")
    pca = None 

# Map motor number to channel
motor_channels = {
    "1": 12,
    "2": 13,
    "3": 14,
    "4": 15
}

# --- CALIBRATED CONSTANTS ---
# UPDATED based on user feedback: The ESCs arm/beep when set to 120.
# ARMING_ANGLE and NEUTRAL_ANGLE are now identical to perform the
# safe "arm at minimum pulse" sequence.
ARMING_ANGLE = 120  # Confirmed signal where the ESC arms/beeps (minimum safe pulse)
NEUTRAL_ANGLE = 120 # Confirmed angle where the motor stops (minimum safe pulse)
FORWARD_ANGLE = 180 # Confirmed max throttle angle for full speed control
REVERSE_ANGLE = 60  # <-- NEW: Assumes 60° gives max reverse. Common value for 1ms to 2ms range is 0° (1ms), but using 60° as a safe intermediate/test value is common for reverse.

# State dictionary to track the current angle for each motor
motor_states = {m: NEUTRAL_ANGLE for m in motor_channels.keys()}
# Lock to ensure thread-safe access to motor_states
motor_state_lock = threading.Lock() 

# --------------------------
# PWM Helper 
# --------------------------
def angle_to_pwm(angle):
    """Converts a simulated angle (0-180) to a 16-bit PWM duty cycle."""
    # Standard servo pulse range: 0° is 1ms, 180° is 2ms.
    # We map the 0-180 range to the standard 1000us to 2000us pulse width.
    pulse_us = 1000 + (angle / 180.0) * 1000
    # Convert to 16-bit duty cycle (0-65535) for 50Hz (20ms period)
    duty = int((pulse_us / 20000.0) * 65535)
    return duty

# --------------------------
# Utility Functions
# --------------------------
def set_duty_cycle(channel, angle):
    """Sets the duty cycle for a specific PCA channel based on angle."""
    if pca is None:
        # Running in simulation mode
        print(f"SIMULATION: Setting Ch {channel} to {angle}°")
        return
        
    duty = angle_to_pwm(angle)
    try:
        pca.channels[channel].duty_cycle = duty
    except Exception as e:
        print(f"Error setting PWM on channel {channel}: {e}")

def initialize_escs():
    """Performs the robust three-step initialization based on confirmed values."""
    if pca is None:
        print("SIMULATION: ESC initialization skipped due to hardware error.")
        return

    channels_to_init = list(motor_channels.values())
    
    print("\n--- ESC Initialization Sequence (Arm at 120°) ---")
    
    # 1. Arming Pulse: Set all motors to the minimum safe pulse (120°). 
    # This pulse is what the ESC requires to arm/beep.
    print(f"STEP 1: Setting all motors to ARMING_ANGLE ({ARMING_ANGLE}°). ESCs should arm/beep now.")
    for ch in channels_to_init:
        set_duty_cycle(ch, ARMING_ANGLE)
    
    # Hold for a few seconds to let ESCs recognize the signal and arm.
    time.sleep(3.0) 
    
    # 2. Safety Check: Move to 0 duty cycle (minimum electrical signal) to ensure ESCs 
    # are ready to receive commands from a true safety stop.
    print("STEP 2: Safety Minimum (0 duty cycle) for 1 second.")
    for ch in channels_to_init:
        # Set to 0. This is the 0-pulse/disarm signal.
        pca.channels[ch].duty_cycle = 0 
    time.sleep(1.0)
    
    # 3. Neutral State: Move back to the confirmed 120° to complete arming and hold neutral.
    print(f"STEP 3: Moving to confirmed NEUTRAL_ANGLE ({NEUTRAL_ANGLE}°). Motors should be stopped and armed.")
    for ch in channels_to_init:
        set_duty_cycle(ch, NEUTRAL_ANGLE)
    time.sleep(2.0)
    
    # Update all internal states to the NEUTRAL_ANGLE
    with motor_state_lock:
        for motor_num in motor_states.keys():
            motor_states[motor_num] = NEUTRAL_ANGLE
    
    print(f"ESCs initialized and armed at neutral ({NEUTRAL_ANGLE}°) - motors stopped.")

# --------------------------
# Motor Control 
# --------------------------
# --------------------------
# Motor Control (SAFE VERSION)
# --------------------------
def set_motor_speed(motor_num, target_angle):
    """Safely transitions motor speed, enforcing a stop for direction reversal."""
    
    if motor_num not in motor_channels:
        print(f"ERROR: Invalid motor number: {motor_num}")
        return

    ch = motor_channels[motor_num]
    
    # 1. READ CURRENT STATE (Inside lock)
    with motor_state_lock:
        current_angle = motor_states[motor_num]
    
    # Check if we are already at the target
    if current_angle == target_angle:
        print(f"Motor {motor_num}: Already at target angle {target_angle}°")
        return
        
    print(f"Motor {motor_num}: Changing speed/direction from {current_angle}° to {target_angle}°.")

    # Clamp the target angle for safety
    clamped_target = max(REVERSE_ANGLE, min(FORWARD_ANGLE, target_angle)) 
    
    # --- CRITICAL SAFETY BLOCK: Enforce Neutral Stop for Reversal ---
    # Only perform the intermediate stop if the motor is currently running 
    # and the target is not neutral (i.e., changing direction or running to a different speed)
    if current_angle != NEUTRAL_ANGLE and clamped_target != NEUTRAL_ANGLE:
        
        print(f"Motor {motor_num}: Intermediate Stop ({NEUTRAL_ANGLE}°) for 0.1s to ensure safe reversal.")
        
        # Step A: STOP
        set_duty_cycle(ch, NEUTRAL_ANGLE)
        # Update state immediately (crucial for accurate tracking)
        with motor_state_lock:
            motor_states[motor_num] = NEUTRAL_ANGLE 
            
        # Step B: DELAY
        time.sleep(0.1) # Wait 100ms for the ESC to register the neutral signal
        
 # --------------------------
# --------------------------
# Motor Control (WITH RAMP-UP AND NAMEERROR FIX)
# --------------------------
def set_motor_speed(motor_num, target_angle):
    """Safely transitions motor speed, enforcing a stop for reversal AND implementing a soft start."""
    
    if motor_num not in motor_channels:
        print(f"ERROR: Invalid motor number: {motor_num}")
        return

    ch = motor_channels[motor_num]
    
    # 1. READ CURRENT STATE (Inside lock) - FIX for NameError
    with motor_state_lock:
        # 'current_angle' is now guaranteed to be defined before any 'if' statement uses it
        current_angle = motor_states[motor_num]
    
    # Check if we are already at the target
    if current_angle == target_angle:
        print(f"Motor {motor_num}: Already at target angle {target_angle}°")
        return
        
    print(f"Motor {motor_num}: Changing speed/direction from {current_angle}° to {target_angle}°.")

    # Clamp the target angle for safety
    clamped_target = max(REVERSE_ANGLE, min(FORWARD_ANGLE, target_angle)) 
    
    # --- CRITICAL SAFETY BLOCK: Enforce Neutral Stop for Reversal ---
    # Only perform the intermediate stop if the motor is currently running 
    # and the target is not neutral (i.e., changing direction or running to a different speed)
    if current_angle != NEUTRAL_ANGLE and clamped_target != NEUTRAL_ANGLE:
        
        print(f"Motor {motor_num}: Intermediate Stop ({NEUTRAL_ANGLE}°) for 0.1s to ensure safe reversal.")
        
        # Step A: STOP
        set_duty_cycle(ch, NEUTRAL_ANGLE)
        # Update state immediately (crucial for accurate tracking)
        with motor_state_lock:
            motor_states[motor_num] = NEUTRAL_ANGLE 
            
        # Step B: DELAY
        time.sleep(0.1) # Wait 100ms for the ESC to register the neutral signal
        
        # Update current_angle to NEUTRAL_ANGLE for the subsequent ramp-up logic
        current_angle = NEUTRAL_ANGLE 
        
    # --- RAMP-UP / SOFT START LOGIC ---
    
    # Define ramp parameters
    steps = 15     # Number of steps for a smooth change
    delay_s = 0.02 # Delay between steps (15 * 0.02 = 0.3 seconds total ramp time)
    
    # Check if we are starting a motor from a neutral/stop state
    if current_angle == NEUTRAL_ANGLE and clamped_target != NEUTRAL_ANGLE:
        
        print(f"Motor {motor_num}: Starting smooth speed transition to {clamped_target}°.")
        
        start_angle = NEUTRAL_ANGLE
        # Calculate the required size of each step
        step_size = (clamped_target - start_angle) / steps
        
        current_angle_in_ramp = start_angle
        
        # Perform the ramp
        for i in range(1, steps + 1):
            
            # Calculate the next angle
            current_angle_in_ramp += step_size
            
            # Clamp to the final target to avoid overshoot
            if step_size > 0: # Forward direction (from 120 up to 180)
                next_angle = min(clamped_target, current_angle_in_ramp)
            else: # Reverse direction (from 120 down to 60)
                next_angle = max(clamped_target, current_angle_in_ramp)
                
            set_duty_cycle(ch, next_angle)
            
            # Update state *during* the ramp for accurate tracking
            with motor_state_lock:
                motor_states[motor_num] = next_angle 
                
            time.sleep(delay_s)
            
    # --- FINAL COMMAND ---
    # This runs for the final precise command, OR if we were just changing speed 
    # within the same direction (e.g., 150 -> 180), skipping the ramp logic above.
    
    print(f"Motor {motor_num}: Final speed command to {clamped_target}°.")
    set_duty_cycle(ch, clamped_target)
    
    # Update state to the final target
    with motor_state_lock:
        motor_states[motor_num] = clamped_target
            
    print(f"Motor {motor_num} holding at {clamped_target}°")

# --------------------------
# Clean up and Exit Handlers
# --------------------------
def stop_all_motors():
    """Sets all PWMs to 0 (off) and cleans up PCA9685."""
    print("\nStopping all motors and cleaning up...")
    
    if pca is not None:
        for ch in motor_channels.values():
            try:
                # Set to 0 duty cycle (minimum pulse width, safe stop)
                pca.channels[ch].duty_cycle = 0 
            except:
                pass
        
        try:
            pca.deinit()
        except Exception as e:
            print(f"Error de-initializing PCA9685: {e}")
    else:
        print("Cleanup skipped (PCA not initialized).")
    
    print("Done.")

# Register cleanup on normal and forceful exit
atexit.register(stop_all_motors)

def handle_exit(sig, frame):
    stop_all_motors()
    # Use sys.exit(0) for a cleaner exit, especially since we're using atexit
    sys.exit(0) 

signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

# --------------------------
# Flask App
# --------------------------
app = Flask(__name__)

@app.route("/motor/<motor_num>", methods=["POST"])
def motor_control(motor_num):
    """API endpoint to control a single motor's speed (forward/stop/reverse)."""
    if motor_num not in motor_channels:
        return jsonify({"status": "error", "message": f"Invalid motor number: {motor_num}. Available: {list(motor_channels.keys())}"}), 400

    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON data provided"}), 400
            
        action = data.get("action", "").lower()
    except Exception:
        return jsonify({"status": "error", "message": "Invalid JSON format"}), 400

    print(f"\n>>> Motor {motor_num}: Action '{action}'")

    target_angle = None
    if action == "forward":
        # Motor 1 and 3 are clockwise, 2 and 4 are counter-clockwise on UP (FORWARD)
        if motor_num in ["1", "3"]:
            target_angle = FORWARD_ANGLE
        elif motor_num in ["2", "4"]:
            target_angle = REVERSE_ANGLE # Spin counter-clockwise (Reverse Pulse)
        
    elif action == "stop":
        target_angle = NEUTRAL_ANGLE
        
    elif action == "reverse": # <-- NEW ACTION HANDLER
        # Motors reverse direction on DOWN (REVERSE)
        if motor_num in ["1", "3"]:
            target_angle = REVERSE_ANGLE # Spin counter-clockwise (Reverse Pulse)
        elif motor_num in ["2", "4"]:
            target_angle = FORWARD_ANGLE # Spin clockwise (Forward Pulse)
            
    else:
        return jsonify({"status": "error", "message": "Invalid action. Use 'forward', 'reverse', or 'stop'."}), 400
    
    # START ASYNCHRONOUS EXECUTION
    # Start the motor speed transition in a separate thread.
    thread = threading.Thread(target=set_motor_speed, args=(motor_num, target_angle))
    thread.start()

    # The Flask handler returns immediately, preventing the client timeout.
    return jsonify({
        "status": "command_accepted", 
        "motor": motor_num, 
        "action": action, 
        "message": f"Speed transition for {action} started asynchronously."
    })

@app.route("/stop_all", methods=["POST"])
def stop_all_route():
    """API endpoint to stop all motors."""
    print("\n>>> STOP ALL COMMAND RECEIVED (Asynchronous)")
    for motor_num in motor_channels.keys():
        # Start ramp down to neutral in a separate thread for each motor
        thread = threading.Thread(target=set_motor_speed, args=(motor_num, NEUTRAL_ANGLE))
        thread.start()
        
    return jsonify({"status": "command_accepted", "message": "All motors commanded to stop (asynchronously)."}), 202

@app.route("/status", methods=["GET"])
def get_status():
    """Returns the current state of all motors and calibration constants."""
    # Acquire lock before reading the state to ensure thread safety
    with motor_state_lock:
        current_motor_states = motor_states.copy()
        
    return jsonify({
        "status": "running",
        "motor_states": current_motor_states,
        "calibration_constants": {
            "ARMING_PULSE": ARMING_ANGLE,
            "NEUTRAL_ANGLE": NEUTRAL_ANGLE,
            "FORWARD_ANGLE": FORWARD_ANGLE,
            "REVERSE_ANGLE": REVERSE_ANGLE, # <-- ADDED
        },
        "hardware_initialized": pca is not None
    })

@app.route("/shutdown", methods=["POST"])
def shutdown_route():
    """API endpoint to safely shut down the server and motors."""
    print("\n>>> SHUTDOWN COMMAND RECEIVED")
    stop_all_motors()
    sys.exit(0) # Terminate the process cleanly
    return jsonify({"status": "ok", "message": "System is shutting down."})

# --------------------------
# Main
# --------------------------
if __name__ == "__main__":

    print("\n" + "="*50)
    print("Initializing ESCs with calibrated values...")
    print("="*50 + "\n")
    
    initialize_escs()
    
    print("\n" + "="*50)
    print(f"SERVER READY. Listen on port 5001 (ASYNCHRONOUS CONTROL).")
    print("Use GET /status to check current motor angles.")
    print("="*50 + "\n")
    
    app.run(host="0.0.0.0", port=5001, threaded=True, use_reloader=False)
