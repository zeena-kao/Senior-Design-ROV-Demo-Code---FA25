# Senior-Design-ROV-Demo-Code---FA25

**Motor Server (Terminal 1: Payload Pi):** 
1. Open Virtual Environment
pi@raspberrypi:~/.local $ source /home/pi/.local/pca_env/bin/activate
(pca_env) pi@raspberrypi: :~/.local $ 

3. Run Motor Server 
(pca_env) pi@raspberrypi:~/.local $ /home/pi/.local/ina260env/bin/python /home/pi/.local/NEW_server_flask.py

**UI Server (Terminal 2: Payload Pi):**
1. Open a new terminal
2. Ensure you're not in a virtual environment anymore 
If in pca_env:
	(pca_env) pi@raspberrypi: :~/.local $ deactivate 
Ensure: 
	pi@raspberrypi:~/.local $

3. Run UI Server
pi@raspberrypi:~/.local $ /usr/bin/python /home/pi/.local/UI_server.py

**Controller Server (Terminal 1: Controller Pi):**
1. Run Switches
pi@raspberrypi:~/ME74 $ python3 /home/pi/ME74/NEW_switch_client_flask.py

