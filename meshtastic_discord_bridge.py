import discord
import asyncio
import os
import sys
import io
from dotenv import load_dotenv
from pubsub import pub
import meshtastic
import meshtastic.tcp_interface
import meshtastic.serial_interface
import queue
import time
import logging
from datetime import datetime

load_dotenv()
token = os.getenv("DISCORD_TOKEN")
channel_id = int(os.getenv("DISCORD_CHANNEL_ID"))
meshtastic_hostname = os.getenv("MESHTASTIC_HOSTNAME")
node_list= []
local_channels = []

meshtodiscord = queue.Queue()
discordtomesh = queue.Queue()
nodelistq = queue.Queue()

def onConnectionMesh(interface, topic=pub.AUTO_TOPIC):  
    """called when we (re)connect to the meshtastic radio"""
    print(interface.myInfo)

def onReceiveMesh(packet, interface):  
    """called when a packet arrives from mesh"""
    try: 
        if packet['decoded']['portnum']=='TEXT_MESSAGE_APP': #only interest in text packets for now
            fromId=packet['fromId']
            fromName = next((node.longname for node in node_list if node.id == fromId), "Unknown")
            channelIndex = packet['channel']
            channelName = "Primary" if channelIndex == 0 else next((channel.settings.name for channel in local_channels[1:] if channel.index == channelIndex), "CH" + str(channelIndex))
            meshtodiscord.put( "# 📨 New " +  ("DM" if packet['toId'].startswith("!") else ("Message in " + channelName)) + "\n**" + fromName + "** (" + fromId + "):\n> " + packet['decoded']['text'])
    except KeyError as e: #catch empty packet
        pass

class Node:
    def __init__(self, id, num, shortname, longname, hopsaway, snr, lastheardutc):
        self.id = id
        self.num = num
        self.shortname = shortname
        self.longname = longname
        self.hopsaway = hopsaway
        self.snr = snr
        self.lastheardutc = lastheardutc

    def __str__(self):
        return f"__**{self.shortname}**__  **{self.longname}** (num:`{self.num}`, id:`{self.id}`, hops:`{self.hopsaway}`, snr:`{self.snr}`, lastheardutc:`{self.lastheardutc}`)"

class MyClient(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def setup_hook(self) -> None:
        # create the background task and run it in the background
        self.bg_task = self.loop.create_task(self.my_background_task())

    async def on_ready(self):
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('------')

    async def on_connection(self):  # pylint: disable=unused-argument
        # nothing to do here
        return


    async def on_message(self, message):
        if message.author.id == self.user.id:
            return

        if message.content.startswith('$help'):
            helpmessage="Meshtastic Discord Bridge is up.  Command list:\n"\
                "$sendprimary <message> sends a message up to 225 characters to the the primary channel\n"\
                "$send nodenum=########### <message> sends a message up to 225 characters to nodenum ###########\n"\
                "$activenodes will list all nodes seen in the last 15 minutes"
            await message.channel.send(helpmessage)

        if message.content.startswith('$sendprimary'):
            tempmessage=str(message.content)
            tempmessage=tempmessage[tempmessage.find(' ')+1:225] #could be 228
            await message.channel.send('Sending to the primary channel:\n> '+tempmessage)
            discordtomesh.put(tempmessage)

        if message.content.startswith('$send nodenum='):
            tempmessage=str(message.content)
            nodenumstr=tempmessage[14:tempmessage.find(' ',14)+1]
            tempmessage=tempmessage[tempmessage.find(' ',14)+1:225] #could be 228
            try:
                nodenum=int(nodenumstr)
                await message.channel.send('Sending  to **' + str(nodenum) + '**:\n> ' + tempmessage)
                discordtomesh.put("nodenum="+str(nodenum)+ " "+tempmessage)
            except:
                await message.channel.send('Could not send message')
        if message.content.startswith('$activenodes'):
            nodelistq.put("just pop a message on this queue so we know to send nodelist to discord")
            
    async def connect_to_meshtastic(self):
        try:
            if len(meshtastic_hostname) > 1:
                print("Trying TCP interface to " + meshtastic_hostname)
                iface = meshtastic.tcp_interface.TCPInterface(meshtastic_hostname)
            else:
                print("Trying serial interface")
                iface = meshtastic.serial_interface.SerialInterface()
        except Exception as ex:
            print(f"Error: Could not connect {ex}")
            sys.exit(1)
        return iface


    async def my_background_task(self):
        await self.wait_until_ready()
        counter = 0
        channel = self.get_channel(channel_id) 
        pub.subscribe(onReceiveMesh, "meshtastic.receive")
        pub.subscribe(onConnectionMesh, "meshtastic.connection.established")
        iface = await self.connect_to_meshtastic()
        while not self.is_closed():
            counter += 1
            print(str(counter))
            if (counter%12==11):
                iface = await self.connect_to_meshtastic()
            if (counter%12==1):
                #approx 1 minute (every 12th call, call every 5 seconds), refresh node list
                for localChannel in iface._localChannels:
                    local_channels.append(localChannel)
                nodes=iface.nodes
                for node in nodes:
                    try:
                            id = str(nodes[node]['user']['id'])
                            num = str(nodes[node]['num'])
                            shortname = str(nodes[node]['user']['shortName'])
                            longname = str(nodes[node]['user']['longName'])
                            if "hopsAway" in nodes[node]:
                                hopsaway = str(nodes[node]['hopsAway'])
                            else:
                                hopsaway="0"
                            if "snr" in nodes[node]:
                                snr = str(nodes[node]['snr'])
                            else:
                                snr="?"
                            if "lastHeard" in nodes[node]:
                                ts=int(nodes[node]['lastHeard'])
                                timestr = datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
                            else:
                                #Just make it old so it doesn't show, only interested in nodes we know are active
                                #Use this if you want to assign a time in the past: ts=time.time()-(16*60)
                                timestr="Unknown"
                            #Use this if you want to filter on time: if ts>time.time()-(15*60):
                            node_list.append(Node(id, num, shortname, longname, hopsaway, snr, timestr))
                            # print(node_list[-1])
                    except KeyError as e:
                        print(e)
                        pass
                print("Node list updated")
            try:
                meshmessage=meshtodiscord.get_nowait()
                await channel.send(meshmessage)
                meshtodiscord.task_done()
            except queue.Empty:
                pass
            try:
                meshmessage=discordtomesh.get_nowait()
                if meshmessage.startswith('nodenum='):
                    nodenum=int(meshmessage[8:meshmessage.find(' ')])
                    iface.sendText(meshmessage[meshmessage.find(' ')+1:],destinationId=nodenum)
                else:    
                    iface.sendText(meshmessage)
                discordtomesh.task_done()
            except: #lets pass on both the empty queue and the int conversion
                pass
            try:
                nodelistq.get_nowait()
                #if there's any item on this queue, we'll send the nodelist
                packet=""
                for node in node_list:
                    if len(packet)+len(str(node)) < 1900:
                        packet=packet+str(node)+"\n"
                    else:
                        await channel.send(packet)
                        packet=str(node)+"\n"
                await channel.send(packet)
                nodelistq.task_done()
            except queue.Empty:
                pass
            await asyncio.sleep(5) 
        
intents=discord.Intents.default()
intents.message_content = True

client = MyClient(intents=intents)
client.run(token)
