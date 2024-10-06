from flask import Flask, send_file
from flask_socketio import SocketIO
from threading import Thread
from loguru import logger
import socket
import json
import os
import websocket
import time
import sys

debug = False
log_level = "INFO"
if os.environ.get("DEBUG") is not None and os.environ.get("DEBUG"):
    debug = True
    log_level = "DEBUG"

logger.add(sys.stdout, colorize=debug, level=log_level)

port = 54780
discovery_timeout = 1
app = Flask(__name__,
            static_url_path='',
            static_folder='web')
socketio = SocketIO(app)
websockets = {}
printers = {}


@app.route("/")
def web_index():
    return app.send_static_file('index.html')


@socketio.on('connect')
def sio_handle_connect(auth):
    logger.info('Client connected')
    socketio.emit('printers', printers)


@socketio.on('disconnect')
def sio_handle_disconnect():
    logger.info('Client disconnected')


@socketio.on('printers')
def sio_handle_printers(data):
    logger.debug('client.printers >> '+data)
    main()


@socketio.on('printer_info')
def sio_handle_printer_status(data):
    logger.debug('client.printer_info >> '+data['id'])
    get_printer_status(data['id'])
    get_printer_attributes(data['id'])


@socketio.on('printer_files')
def sio_handle_printer_files(data):
    logger.debug('client.printer_files >> '+json.dumps(data))
    get_printer_files(data['id'], data['url'])


def get_printer_status(id):
    send_printer_cmd(id, 0)


def get_printer_attributes(id):
    send_printer_cmd(id, 1)


def get_printer_files(id, url):
    send_printer_cmd(id, 258, {"Url": url})


def send_printer_cmd(id, cmd, data={}):
    printer = printers[id]
    ts = int(time.time())
    payload = {
        "Id": printer['connection'],
        "Data": {
            "Cmd": cmd,
            "Data": data,
            "RequestID": os.urandom(8).hex(),
            "MainboardID": id,
            "TimeStamp": ts,
            "From": 0
        },
        "Topic": "sdcp/request/" + id
    }
    logger.debug("printer << \n{p}", p=json.dumps(payload, indent=4))
    if id in websockets:
        websockets[id].send(json.dumps(payload))


def discover_printers():
    logger.info("Starting printer discovery.")
    msg = b'M99999'
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                         socket.IPPROTO_UDP)  # UDP
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(discovery_timeout)
    sock.bind(('', 54781))
    sock.sendto(msg, ("255.255.255.255", 3000))
    socketOpen = True
    printers = None
    while (socketOpen):
        try:
            data = sock.recv(8192)
            printers = save_discovered_printer(data)
        except TimeoutError:
            sock.close()
            break
    logger.info("Discovery done.")
    return printers


def save_discovered_printer(data):
    j = json.loads(data.decode('utf-8'))
    printer = {}
    printer['connection'] = j['Id']
    printer['name'] = j['Data']['Name']
    printer['model'] = j['Data']['MachineName']
    printer['brand'] = j['Data']['BrandName']
    printer['ip'] = j['Data']['MainboardIP']
    printer['protocol'] = j['Data']['ProtocolVersion']
    printer['firmware'] = j['Data']['FirmwareVersion']
    printers[j['Data']['MainboardID']] = printer
    logger.info("Discovered: {n} ({i})".format(
        n=printer['name'], i=printer['ip']))
    return printers


def connect_printers(printers):
    for id, printer in printers.items():
        url = "ws://{ip}:3030/websocket".format(ip=printer['ip'])
        logger.info("Connecting to: {n}".format(n=printer['name']))
        websocket.setdefaulttimeout(1)
        ws = websocket.WebSocketApp(url,
                                    on_message=ws_msg_handler,
                                    on_open=lambda _: ws_connected_handler(
                                        printer['name']),
                                    on_close=lambda _, s, m: logger.info(
                                        "Connection to '{n}' closed: {m} ({s})".format(n=printer['name'], m=m, s=s)),
                                    on_error=lambda _, e: logger.info(
                                        "Connection to '{n}' error: {e}".format(n=printer['name'], e=e))
                                    )
        websockets[id] = ws
        Thread(target=lambda: ws.run_forever(reconnect=1), daemon=True).start()

    return True


def ws_connected_handler(name):
    logger.info("Connected to: {n}".format(n=name))
    socketio.emit('printers', printers)


def ws_msg_handler(ws, msg):
    data = json.loads(msg)
    logger.debug("printer >> \n{m}", m=json.dumps(data, indent=4))
    if data['Topic'].startswith("sdcp/response/"):
        socketio.emit('printer_response', data)
    elif data['Topic'].startswith("sdcp/status/"):
        socketio.emit('printer_status', data)
    elif data['Topic'].startswith("sdcp/attributes/"):
        socketio.emit('printer_attributes', data)
    elif data['Topic'].startswith("sdcp/error/"):
        socketio.emit('printer_error', data)
    elif data['Topic'].startswith("sdcp/notice/"):
        socketio.emit('printer_notice', data)
    else:
        logger.warning("--- UNKNOWN MESSAGE ---")
        logger.warning(data)
        logger.warning("--- UNKNOWN MESSAGE ---")


def main():
    printers = discover_printers()
    if len(printers) > 0:
        connect_printers(printers)
        logger.info("Setting up connections: done.")
        socketio.emit('printers', printers)
    else:
        logger.error("No printers discovered.")


if __name__ == "__main__":
    main()

    socketio.run(app, host='0.0.0.0', port=port,
                 debug=debug, use_reloader=debug, log_output=True)
