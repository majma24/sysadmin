#!/usr/bin/env python3
from paramiko import SSHClient, RSAKey
from .humanize import approximate_size
import os
import math
import string
import sys
import argparse
import configparser

naparser = argparse.ArgumentParser()
naparser.add_argument('-c', '--configfile', dest='configfile',
          help='specify a config file (it must exist), if config file is not specified, it defaults to na.ini in the current directory')

def find_config(args):
  """
  find the config file

  defaults to na.ini in the working directory
  """
  configfile = None

  if args.configfile:
    configfile = args.configfile
  else:
    configfile = os.path.join(os.getcwd(), "na.ini")

  if not os.path.join(configfile):
    thelp = naparser.format_usage()
    thelp = thelp + '\n' + "Config file %s does not exist" % configfile
    thelp = thelp + '\n  ' + configfile

    print(thelp)
    sys.exit(1)

  return configfile

BYTE = 1000

NUMBYTES = {}
NUMBYTES['KB'] = float(BYTE)
NUMBYTES['MB'] = NUMBYTES['KB'] * BYTE
NUMBYTES['GB'] = NUMBYTES['MB'] * BYTE
NUMBYTES['TB'] = NUMBYTES['GB'] * BYTE

def convertnetappsize(size):
  ts = size[-2:]
  if ts in NUMBYTES:
    num = size[0:-2]
    num = float(num.strip())
    newsize = num * NUMBYTES[ts]
    return newsize
  else:
    return size

class Volume:
  def __init__(self, name, svm):
    self.name = name
    self.svm = svm
    self.luns = {}
    self.attr = {}

  def sset(self, key, value):
    self.attr[key] = value

  def addlun(self, lun):
    lunname = lun.attr['LUN Name']
    self.luns[lunname] = lun

class LUN:
  def __init__(self, name, svm, data=None):
    self.name = name
    self.svm = svm
    if not data:
      self.attr = {}
    else:
      self.attr = data

  def sset(self, key, value):
    self.attr[key] = value

class SVM:
  def __init__(self, name, cluster):
    self.name = name
    self.hasvolumes = False
    self.hasluns = False
    self.cluster = cluster
    self.volumes = {}
    self.luns = {}
    self.attr = {}

  def fetchvolumes(self):
    if not self.hasvolumes:
      cmd = 'vol show -vserver %s -instance' % self.name
      output = self.cluster.runcmd(cmd, excludes=['Vserver Name'])
      currentvolume = None
      for line in output:
        if not line:
          continue
        if 'Volume Name' in line:
          currentvolume = line.split(':', 1)[1].strip()
          self.volumes[currentvolume] = Volume(currentvolume, self)
        else:
          line = line.strip()
          tlist = line.split(':', 1)
          key = tlist[0].strip()
          value = tlist[1].strip()
          if 'size' in key or 'Size' in key:
            nvalue = convertnetappsize(value)
            if nvalue != value:
              value = nvalue
          self.volumes[currentvolume].sset(key, value)
      self.hasvolumes = True

  def fetchluns(self):
    if not self.hasvolumes:
      self.fetchvolumes()

    if not self.hasluns:
      cmd = 'lun show -vserver %s -instance' % self.name
      output = self.cluster.runcmd(cmd, excludes=['Vserver Name', 'This operation is only supported on data Vservers', 'has type "node"', 'has type "admin"'])
      currentlun = {}
      for line in output:
        if not line:
          continue

        if 'LUN Path' in line:
          if currentlun:
            self.luns[currentlun['LUN Name']] = LUN(currentlun['LUN Name'], self, data = currentlun)
            if currentlun['Volume Name'] in self.volumes:
              self.volumes[currentlun['Volume Name']].addlun(self.luns[currentlun['LUN Name']])
            else:
              print('Could not find parent volume %s for lun %s' % (currentlun['Volume Name'], currentlun['LUN Name']))
            currentlun = {}

        line = line.strip()
        tlist = line.split(':', 1)
        if len(tlist) == 2:
          key = tlist[0].strip()
          value = tlist[1].strip()

          if 'size' in key or 'Size' in key:
            nvalue = convertnetappsize(value)
            if nvalue != value:
              value = nvalue
          currentlun[key] = value
        else:
          if len(line) == 1:
            print(ord(line))
          print('Invalid line in fetchluns', line, )

      if currentlun:
        self.luns[currentlun['LUN Name']] = LUN(currentlun['LUN Name'], self, data = currentlun)
        if currentlun['Volume Name'] in self.volumes:
          self.volumes[currentlun['Volume Name']].addlun(self.luns[currentlun['LUN Name']])
        else:
          print('Could not find parent volume %s for lun %s' % (currentlun['Volume Name'], currentlun['LUN Name']))
        currentlun = {}

      self.hasluns = True

  def sset(self, key, value):
    self.attr[key] = value

class Cluster:
  def __init__(self, address, cname, username=None, pw=None, keyfile=None, keyfile_pw=None):
    self.address = address
    self.cname = cname

    self.name = None
    self.uuid = None
    self.serialnumber = None
    self.location = None
    self.contact = None

    self.svms = {}
    self.snapmirrors = []
    self.peers = {}
    self.peersrev = {}

    self.ssh = SSHClient()
    self.ssh.load_system_host_keys()

    self.username = username
    self.pw = pw
    self.pkey_filename = keyfile
    self.pkey_pw = keyfile_pw

    self.pkey = None
    if self.pkey_filename:
      self.pkey = RSAKey.from_private_key_file(self.pkey_filename, password=self.pkey_pw)

    self.fetchclusterinfo()
    self.fetchsvms()

  def fetchclusterinfo(self):
    output = self.runcmd('cluster identity show')
    for line in output:
      if not line:
        continue
      if 'Cluster UUID:' in line:
        self.uuid = line.split(':', 1)[1].strip()
      if 'Cluster Name:' in line:
        self.name = line.split(':', 1)[1].strip()
      if 'Cluster Serial Number:' in line:
        self.serialnumber = line.split(':')[1].strip()
      if 'Cluster Location:' in line:
        tlist = line.split(':', 1)
        if len(tlist) > 1:
          self.location = line.split(':', 1)[1].strip()
      if 'Cluster Contact:' in line:
        tlist = line.split(':', 1)
        if len(tlist) > 1:
          self.contact = line.split(':', 1)[1].strip()

  def fetchpeers(self):
    output = self.runcmd('vserver peer show-all -instance')
    peer = {}
    for line in output:
      if not line:
        continue
      if 'Local Vserver Name' in line:
        if peer:
          self.peers[peer['Local Vserver Name']] = peer
          self.peersrev[peer['Peer Vserver Name']] = peer
          peer = {}
        local = line.split(':', 1)[1].strip()
        peer['Local Vserver Name'] = local
      elif 'Peer Vserver Name' in line:
        peername = line.split(':', 1)[1].strip()
        peer['Peer Vserver Name'] = peername
      else:
        line = line.strip()
        tlist = line.split(':', 1)
        slist = [x.strip() for x in tlist]
        try:
          peer[slist[0]] = slist[1]
        except IndexError:
          print('The following line was malformed')
          print(line)

  def fetchsnapmirrors(self):
    output = self.runcmd('snapmirror show -instance')
    count = 0
    cursnap = {}
    for line in output:
      if not line:
        continue
      if 'Source Path:' in line:
        if cursnap:
          self.snapmirrors.append(cursnap)
          cursnap = {}
        tlist = line.split(':')
        cursnap['Source Path'] = {'svm':tlist[1].strip(), 'vol':tlist[2].strip()}
      elif 'Destination Path' in line:
        tlist = line.split(':')
        cursnap['Destination Path'] = {'svm':tlist[1].strip(), 'vol':tlist[2].strip()}
      else:
        line = line.strip()
        tlist = line.split(':', 1)
        slist = [x.strip() for x in tlist]
        cursnap[slist[0]] = slist[1]

  def fetchsvms(self):
    output = self.runcmd('vserver show -instance')
    currentsvm = ''
    lastkey = None
    for line in output:
      if not line:
        lastkey = None
        continue
      if 'Vserver:' in line:
        currentsvm = line.split()[1]
        self.svms[currentsvm] = SVM(currentsvm, self)
      else:
        line = line.strip()
        tlist = line.split(':', 1)
        slist = [x.strip() for x in tlist]
        try:
          self.svms[currentsvm].sset(slist[0], slist[1])
          lastkey = slist[0]
        except IndexError:
          if lastkey and len(slist) == 1:
            if type(self.svms[currentsvm].attr[lastkey]) == list:
              self.svms[currentsvm].attr[lastkey].append(slist[0])
            else:
              nvalue = [self.svms[currentsvm].attr[lastkey], slist[0]]
              self.svms[currentsvm].sset(lastkey, nvalue)

  def runcmd(self, cmd, excludes=None):
    if not self.ssh.get_transport() or not self.ssh.get_transport().is_active():
      if self.pkey:
        self.ssh.connect(self.address, username=self.username, pkey=self.pkey)
      else:
        self.ssh.connect(self.address, username=self.username, password=self.pw)


    if not excludes:
      excludes = []

    excludes.append('entries were displayed')
    excludes.append('There are no entries matching your query.')
    excludes.append('\a')

    output = []
    stdin, stdout, stderr = self.ssh.exec_command(cmd)
    for line in stdout:
      line = line.rstrip()

      if "Press <space> to page down" in line:
        stdin.write(' ')
        stdin.flush()
      else:
        save = True
        if excludes:
          for exc in excludes:
            if exc in line:
              save = False
              break
        if save:
          output.append(line)

    return output

class ClusterManager:
  def __init__(self, args):
    self.args = args

    self.clusters = {}

    self.configfile = find_config(args)
    self.cp = configparser.ConfigParser()
    self.cp.read(self.configfile)

    for section in self.cp.sections():

      if section == 'Credentials':
        continue

      self.clusters[section] = Cluster(self.cp[section]['host'], section,
                         username = self.cp['Credentials']['username'],
                         pw = self.cp['Credentials']['pw'],
                         keyfile = self.cp['Credentials']['keyfile'],
                         keyfile_pw = self.cp['Credentials']['keyfile_pw'],
                         )

  def findvolume(self, volume, svm=None):
    if svm:
      foundsvm = False
      for cluster in self.clusters.values():
        if svm in cluster.svms:
          foundsvm = True
          svmo = cluster.svms[svm]

          svmo.fetchvolumes()

          if volume in svmo.volumes:
            return svmo.volumes[volume]

      if not foundsvm:
        print('Could not find svm %s' % svm)
        return None

      print('Could not find volume %s in svm %s' % (volume, svm))
      return None

    else:
      for cluster in self.clusters.values():
        for svm in cluster.svms.values():
          svm.fetchvolumes()
          if volume in svm.volumes:
            return svm.volumes[volume]

      print('Could not find volume %s' % volume)
      return None

  def findsvm(self, svm):
    for cluster in self.clusters.values():
      if svm in cluster.svms:
        return cluster.svms[svm]

    return None
