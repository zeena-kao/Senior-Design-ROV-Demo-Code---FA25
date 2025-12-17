from flask import Flask, render_template_string, jsonify, Response
import os
import time
from smbus2 import SMBus
from picamera2 import Picamera2
import cv2

app = Flask(__name__)

# ==========================
# I2C Setup and Initialization
# ==========================
# BNO055 (IMU/Accelerometer)
BNO055_ADDRESS = 0x28
OPR_MODE = 0x3D
ACCEL_X_LSB = 0x09
IMU_MODE = 0x08 # NDOF Mode for full features, or ACCEL mode 0x08 for just accel

# INA260 (Current Sensor)
INA260_ADDRESS = 0x40
INA260_CURRENT_REG = 0x01 # Current Register
INA260_VOLTAGE_REG = 0x02 # Bus Voltage Register
INA260_POWER_REG = 0x03 # Power Register

# Initialize I2C Bus (Bus 1 is common for Raspberry Pi)
try:
    i2c_bus = SMBus(1)
    
    # Initialize BNO055 (Set to ACCEL Mode)
    i2c_bus.write_byte_data(BNO055_ADDRESS, OPR_MODE, IMU_MODE)
    time.sleep(0.05)
    print("BNO055 initialized.")
    
except FileNotFoundError:
    print("Warning: SMBus not available (Running on non-Pi environment or bus 1 not found). Sensor data will be mocked.")
    i2c_bus = None
except Exception as e:
    print(f"Error initializing BNO055 or I2C: {e}. Sensor data will be mocked.")
    i2c_bus = None


# ==========================
# Sensor Reading Functions
# ==========================

def read_i2c_reg16(address, reg):
    """Reads a 16-bit register word and swaps bytes (INA260 returns LSB/MSB swapped)."""
    if not i2c_bus:
        return 0
    try:
        raw = i2c_bus.read_word_data(address, reg)
        # Swap LSB and MSB
        raw = ((raw & 0xFF) << 8) | (raw >> 8)
        return raw
    except Exception as e:
        print(f"Error reading I2C register {reg} from {address}: {e}")
        return 0


def read_current_sensor(samples=10):
    """
    Reads and calculates INA260 Voltage, Current, and Power.
    Uses an average of 'samples' readings for stabilization.
    """
    if not i2c_bus:
        return {"voltage": 0.0, "current": 0.0, "power": 0.0}

    # Initialize lists for averaging
    voltage_sum = 0
    current_sum = 0
    power_sum = 0

    for _ in range(samples):
        voltage_raw = read_i2c_reg16(INA260_ADDRESS, INA260_VOLTAGE_REG)
        current_raw = read_i2c_reg16(INA260_ADDRESS, INA260_CURRENT_REG)
        power_raw = read_i2c_reg16(INA260_ADDRESS, INA260_POWER_REG)

        # Conversion factors for INA260 (Default settings):
        voltage_sum += voltage_raw * 0.00125 # 1.25 mV per bit
        current_sum += current_raw * 1.25 # 1.25 mA per LSB
        power_sum += power_raw * 10 # 10 mW per LSB
        
        # A small sleep to allow the sensor to update its registers (usually 4ms or less)
        time.sleep(0.005) 

    # Calculate averages
    avg_voltage = voltage_sum / samples
    avg_current = current_sum / samples
    avg_power = power_sum / samples

    return {
        "voltage": round(avg_voltage, 3), # V
        "current": round(avg_current, 2), # mA
        "power": round(avg_power, 2) # mW
    }

def read_accel():
    """Reads BNO055 accelerometer data (X, Y, Z in g)."""
    if not i2c_bus:
        # Mock data if not initialized
        return {"x": 1.0, "y": 0.5, "z": 9.8} 
        
    try:
        # Read 6 bytes starting from ACCEL_X_LSB
        data = i2c_bus.read_i2c_block_data(BNO055_ADDRESS, ACCEL_X_LSB, 6)
        
        # Convert 16-bit little-endian signed integers (BNO055 is in units of 100 LSB/g)
        x = int.from_bytes(data[0:2], 'little', signed=True) / 100.0
        y = int.from_bytes(data[2:4], 'little', signed=True) / 100.0
        z = int.from_bytes(data[4:6], 'little', signed=True) / 100.0
        
        return {"x": round(x, 2), "y": round(y, 2), "z": round(z, 2)}
    except Exception as e:
        print(f"Error reading BNO055: {e}")
        return {"x": 0.0, "y": 0.0, "z": 0.0}


# ==========================
# Pi Camera Setup
# ==========================
try:
    picam2 = Picamera2()
    # Use a smaller resolution for better streaming performance
    config = picam2.create_video_configuration(main={"size": (320, 240)}) 
    picam2.configure(config)
    picam2.start()
    print("PiCamera2 initialized.")
except Exception as e:
    print(f"Error initializing PiCamera2: {e}. Camera stream will be skipped.")
    picam2 = None

def generate_frames():
    if not picam2:
        return
        
    while True:
        try:
            # Capture frame and encode to JPEG for streaming
            frame = picam2.capture_array()
            
            # --- FIX FOR SWAPPED RED/BLUE CHANNELS ---
            # Picamera2 often outputs RGB, but cv2.imencode/streaming expects BGR channel order.
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR) 
            
            ret, buffer = cv2.imencode('.jpg', frame)
            frame_bytes = buffer.tobytes()
            
            # Yield the JPEG data in the Motion JPEG format
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        except Exception as e:
            print(f"Error generating camera frame: {e}")
            time.sleep(1)


# ==========================
# HTML Template (Final Adjustment for Landscape Phone)
# ==========================
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Underwater Vehicle Laboratory</title>
    <style>
        body { font-family: sans-serif; text-align: center; background: #2c3e50; color: #ecf0f1; }
        h1 { color: #ecf0f1; margin-top: 30px; border-bottom: 2px solid #34495e; padding-bottom: 10px;}
        h2 { color: #f1c40f; margin-top: 20px; }
        .authors { color: #bdc3c7; margin-top: -15px; margin-bottom: 20px; font-size: 0.9em; }
        
        /* Base Dashboard: Stacked by default */
        .dashboard { 
            display: flex; 
            flex-direction: column; 
            flex-wrap: nowrap; 
            align-items: center; 
            gap: 20px; 
            margin-top: 30px; 
            padding: 0 10px; 
        }
        
        /* Card Styling (Full width on small screens by default) */
        .sensor-card { 
            background: #34495e; 
            padding: 20px; 
            border-radius: 10px; 
            width: 100%; 
            max-width: 500px; 
            box-shadow: 0 4px 8px rgba(0,0,0,0.5);
            text-align: left;
        }
        .sensor-card h3 { color: #3498db; margin-bottom: 10px; font-size: 1.2em; }
        .data-point { font-size: 1.1em; margin-bottom: 8px; }
        .value { font-weight: bold; color: #2ecc71; }
        
        /* Camera Container Styling */
        .camera-container { 
            flex-basis: auto; 
            width: 100%; 
            max-width: 640px; 
        }
        .camera-container img { 
            margin-top: 20px; 
            border: 4px solid #3498db; 
            border-radius: 8px; 
            width: 100%; 
            height: auto; 
        }
        
        /* Sensor Wrapper: Full width by default, keeps cards stacked vertically */
        .sensor-wrapper {
            width: 100%;
            display: flex; /* Activate flex for the wrapper */
            flex-direction: column; /* Stack sensor cards vertically on phones */
            align-items: center;
            gap: 20px;
        }

        /* --- Media Query 1: Laptop/Desktop View (>= 768px wide) --- */
        @media (min-width: 768px) {
            /* The overall dashboard remains column stacked (Camera on top, wrapper below) */
            
            /* Sensor Wrapper: Allow sensor cards to be side-by-side */
            .sensor-wrapper {
                flex-direction: row; /* Place cards side-by-side */
                justify-content: center;
                max-width: 1040px; /* Limits the overall width of the two side-by-side cards */
            }
            .sensor-card {
                flex-basis: 500px;
                max-width: 500px;
            }
        }
        
        /* --- Media Query 2: Landscape Phone View (<= 767px wide AND landscape) --- */
        @media (max-width: 767px) and (orientation: landscape) {
            .dashboard {
                /* Allow side-by-side layout for the main elements */
                flex-direction: row; 
                flex-wrap: nowrap;
                justify-content: space-between;
                align-items: flex-start; /* Align content to the top */
            }
            
            /* Camera: Dominates the left side */
            .camera-container {
                flex-basis: 70%; 
                max-width: 70%;
                margin-top: 0;
            }
            .camera-container img {
                width: 100%;
                /* Constrain height to fit in the viewport without pushing elements down */
                max-height: 80vh; 
            }

            /* Sensor Wrapper: Takes the remaining space on the right side */
            .sensor-wrapper {
                flex-basis: 25%; /* Takes about 25% of the screen */
                max-width: 25%;
                width: 100%;
                /* Revert sensor cards to stack vertically inside the wrapper */
                flex-direction: column; 
            }
            .sensor-card {
                width: 100%; /* Fill the 25% wrapper width */
                min-width: unset; /* Remove minimum width constraint */
                padding: 10px; /* Reduce padding for tight space */
            }
            h2 { margin-top: 0; }
        }

    </style>
    <script>
        async function updateSensors() {
            try {
                const response = await fetch('/sensors');
                const data = await response.json();
                
                // Update Accelerometer
                document.getElementById('accel_x').textContent = `${data.accel.x} g`;
                document.getElementById('accel_y').textContent = `${data.accel.y} g`;
                document.getElementById('accel_z').textContent = `${data.accel.z} g`;
                
                // Update Current Sensor
                document.getElementById('current').textContent = `${data.current_sensor.current} mA`;
                document.getElementById('voltage').textContent = `${data.current_sensor.voltage} V`;
                document.getElementById('power').textContent = `${data.current_sensor.power} mW`;
                
            } catch (err) {
                console.error("Failed to fetch sensor data:", err);
                // Display error messages
                document.getElementById('accel_x').textContent = 'Error';
                document.getElementById('cUVurrent').textContent = 'Error';
            }
        }

        // Fetch data every 500ms
        setInterval(updateSensors, 500);
        
        // Initial load
        window.onload = updateSensors;
    </script>
</head>
<body>
    <h1>Underwater Vehicle Laboratory</h1>
    <p class="authors">Zeena Kao, Vanessa Corsi, and Yair Wall Fall 2025</p>

    <div class="dashboard">
        
        <div class="camera-container">
            <h2>📷 Live Camera Stream</h2>
            <img src="/stream" alt="Live Camera Feed">
        </div>
        
        <div class="sensor-wrapper">
            <div class="sensor-card">
                <h3>📈 BNO055 Accelerometer</h3>
                <div class="data-point">X-Axis: <span id="accel_x" class="value">Loading...</span></div>
                <div class="data-point">Y-Axis: <span id="accel_y" class="value">Loading...</span></div>
                <div class="data-point">Z-Axis: <span id="accel_z" class="value">Loading...</span></div>
            </div>
            
            <div class="sensor-card">
                <h3>⚡ INA260 Power Monitor</h3>
                <div class="data-point">Current: <span id="current" class="value">Loading...</span></div>
                <div class="data-point">Voltage: <span id="voltage" class="value">Loading...</span></div>
                <div class="data-point">Power: <span id="power" class="value">Loading...</span></div>
            </div>
        </div>
        
    </div>
    
</body>
</html>
"""
# ==========================
# Flask Routes
# ==========================
@app.route('/')
def home():
    """Renders the main dashboard HTML page."""
    return render_template_string(HTML_PAGE)

@app.route('/sensors')
def sensors():
    """API endpoint to get real-time sensor data as JSON."""
    accel_data = read_accel()
    # Call read_current_sensor with default 10 samples
    current_sensor_data = read_current_sensor() 
    
    return jsonify({
        "accel": accel_data,
        "current_sensor": current_sensor_data
    })

@app.route('/stream')
def stream():
    """Video streaming endpoint for the PiCamera2 feed."""
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

# ==========================
# Run Server and Cleanup
# ==========================
if __name__ == '__main__':
    try:
        print("Starting Flask server...")
        app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
    finally:
        # Ensure resources are closed gracefully
        if picam2:
            print("Stopping PiCamera2...")
            picam2.stop()
        if i2c_bus:
            print("Closing I2C bus...")
            i2c_bus.close()
        print("Server shutdown complete.")
