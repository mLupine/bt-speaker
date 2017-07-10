#!/usr/bin/python

from gi.repository import GLib
from bt_manager.audio import SBCAudioSink
from bt_manager.media import BTMedia
from bt_manager.device import BTDevice
from bt_manager.agent import BTAgent, BTAgentManager
from bt_manager.adapter import BTAdapter
from bt_manager.serviceuuids import SERVICES
from bt_manager.uuid import BTUUID

import dbus
import dbus.mainloop.glib
import signal
import subprocess
import alsaaudio
import math
import configparser
import io
import traceback
from subprocess import call

BTAUDIO_CONFIG_FILE = '/opt/btaudio/config.ini'

# Load config
default_config = u'''
[btaudio]
play_command = aplay -f cd -
connect_command = ogg123 /usr/share/sounds/freedesktop/stereo/service-login.oga
disconnect_command = ogg123 /usr/share/sounds/freedesktop/stereo/service-logout.oga

[bluez]
device_path = /org/bluez/hci0
'''

config = configparser.SafeConfigParser()
config.readfp(io.StringIO(default_config))
config.read(BTAUDIO_CONFIG_FILE)

class PipedSBCAudioSinkWithAlsaVolumeControl(SBCAudioSink):
    """
    An audiosink that pipes the decoded output to a command via stdin.
    The class also sets the volume of an alsadevice
    """
    def __init__(self, path='/endpoint/a2dpsink',
                       command=config.get('btaudio', 'play_command'),
                       #alsa_control=config.get('alsa', 'mixer'),
                       #alsa_id=int(config.get('alsa', 'id')),
                       #alsa_cardindex=int(config.get('alsa', 'cardindex')),
                       buf_size=2560):
        SBCAudioSink.__init__(self, path=path)
        # Start process
        self.process = subprocess.Popen(command, shell=True, bufsize=buf_size, stdin=subprocess.PIPE)
        # Hook into alsa service for volume control
        #self.alsamixer = alsaaudio.Mixer(control=alsa_control,
        #                                 id=alsa_id,
        #                                 cardindex=alsa_cardindex)

    def raw_audio(self, data):
        # pipe to the play command
        self.process.stdin.write(data)

    def volume(self, new_volume):
        # normalize volume
        volume = float(new_volume) / 127.0

        print("Volume changed to %i%%" % (volume * 100.0))

        # it looks like the value passed to alsamixer sets the volume by 'power level'
        # to adjust to the (human) perceived volume, we have to square the volume
        # @todo check if this only applies to the raspberry pi or in general (or if i got it wrong)
        #volume = math.pow(volume, 1.0/3.0)

        # alsamixer takes a percent value as integer from 0-100
       # self.alsamixer.setvolume(int(volume * 100.0))
        call(["amixer", "-q", "sset", "Master", "%d%%" % int(volume*100.0)])

class AutoAcceptSingleAudioAgent(BTAgent):
    """
    Accepts one client unconditionally and hides the device once connected.
    As long as the client is connected no other devices may connect.
    This 'first comes first served' is not necessarily the 'bluetooth way' of
    connecting devices but the easiest to implement.
    """
    def __init__(self):
        BTAgent.__init__(self, auto_authorize_connections=False, cb_notify_on_authorize=self.auto_accept_one, default_pass_key=7131, cb_notify_on_request_confirmation=self.confirm_request, cb_notify_on_request_pin_code=self.pincode_request, cb_notify_on_request_pass_key=self.passkey_request)
        self.adapter = BTAdapter(config.get('bluez', 'device_path'))
        self.allowed_uuids = [ SERVICES["AdvancedAudioDistribution"].uuid, SERVICES["AVRemoteControl"].uuid ]
        self.connected = None
        self.tracked_devices =  []
        self.update_discoverable()

    def update_discoverable(self):
        if bool(self.connected):
            print("Hiding adapter from all devices.")
            self.adapter.set_property('Discoverable', False)
        else:
            print("Showing adapter to all devices.")
            self.adapter.set_property('Discoverable', True)

    def confirm_request(self, *arg):
        print("confirm")
        print(arg)
        return True

    def pincode_request(self, *arg):
        print("pin")
        print(arg)

    def passkey_request(self, *arg):
        print("Passkey request")
        print(arg)
        print("EOR")
        return "7130"

    def auto_accept_one(self, *args):#method, device, uuid):
        #print("lol")
        #print(args)
        #return True
        if not BTUUID(uuid).uuid in self.allowed_uuids: return False
        if self.connected and self.connected != device:
            print("Rejecting device, because another one is already connected. connected_device=%s, device=%s" % (self.connected, device))
            return False

        # track connection state of the device (is there a better way?)
        if not device in self.tracked_devices:
            self.tracked_devices.append(device)
            self.adapter._bus.add_signal_receiver(self._track_connection_state,
                                                  path=device,
                                                  signal_name='PropertiesChanged',
                                                  dbus_interface='org.freedesktop.DBus.Properties',
                                                  path_keyword='device')

        return True

    def _track_connection_state(self, addr, properties, signature, device):
        if self.connected and self.connected != device: return
        if not 'Connected' in properties: return

        if not self.connected and bool(properties['Connected']):
            print("Device connected. device=%s" % device)
            self.connected = device
            self.update_discoverable()
            subprocess.Popen(config.get('btaudio', 'connect_command'), shell=True)
        elif self.connected and not bool(properties['Connected']):
            print("Device disconnected. device=%s" % device)
            self.connected = None
            self.update_discoverable()
            subprocess.Popen(config.get('btaudio', 'disconnect_command'), shell=True)

def setup_bt():
    # setup bluetooth agent (that manages connections of devices)
    agent = AutoAcceptSingleAudioAgent()
    manager = BTAgentManager()
    manager.register_agent(agent._path, "NoInputNoOutput")
    manager.request_default_agent(agent._path)

    # register sink and media endpoint
    sink = PipedSBCAudioSinkWithAlsaVolumeControl()
    media = BTMedia(config.get('bluez', 'device_path'))
    media.register_endpoint(sink._path, sink.get_properties())

def run():
    # Initialize the DBus SystemBus
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

    # Mainloop for communication
    mainloop = GLib.MainLoop()

    # catch SIGTERM
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, lambda signal: mainloop.quit(), None)

    # setup bluetooth configuration
    setup_bt()

    # Run
    mainloop.run()

if __name__ == '__main__':
    try:
        run()
    except KeyboardInterrupt:
        print('KeyboardInterrupt')
    except Exception as e:
        print("Error: %s" % e.message)
        traceback.print_exc()
