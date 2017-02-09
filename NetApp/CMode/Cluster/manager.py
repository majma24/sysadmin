#!/usr/bin/env python3
from paramiko import SSHClient, RSAKey
from .humanize import approximate_size
import os
import math
import string
import sys
import argparse
import configparser
import time
import re

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


class AGGR:
  def __init__(self, name, cluster, data=None):
    self.name = name
    self.cluster = cluster
    if not data:
      self.attr = {}
    else:
      self.attr = data

    self.fixfields()

  def sset(self, key, value):
    self.attr[key] = value

  def fixdiskfield(self, dfield):
    disklist = self.attr[dfield]
    if type(disklist) == list:
      newdisks = []
      for i in disklist:
        disks = i.split(',')
        disks = [x.strip() for x in disks if x]
        newdisks.extend(disks)
      self.attr[dfield] = newdisks
    else:
      if disklist == '-':
        self.attr[dfield] = []
      else:
        disks = disklist.split(',')
        disks = [x.strip() for x in disks if x]
        self.attr[dfield] = disks

  def fixfields(self):
    self.fixdiskfield('Disks for First Plex')
    self.fixdiskfield('Disks for Mirrored Plex')


class Volume:
  def __init__(self, name, svm):
    self.name = name
    self.svm = svm
    self.luns = {}
    self.attr = {}
    self.snaps = {}

  def sset(self, key, value):
    self.attr[key] = value

  def addlun(self, lun):
    lunname = lun.attr['LUN Name']
    self.luns[lunname] = lun

  def addsnap(self, snap):
    snapname = snap['Snapshot']
    self.snaps[snapname] = snap

  def fetchsnapshots(self):
    if not self.snaps:
      cmd = 'snapshot show -vserver %s -volume %s -instance' % (self.svm.name, self.name)
      output = self.svm.cluster.runcmd(cmd, excludes=['   Vserver'])
      currentsnap = {}
      for line in output:
        if not line:
          continue
        if '    Volume' in line and currentsnap:
          self.addsnap(currentsnap)
          currentsnap = {}
        else:
          line = line.strip()
          tlist = line.split(':', 1)
          key = tlist[0].strip()
          value = tlist[1].strip()
          if 'size' in key or 'Size' in key:
            nvalue = convertnetappsize(value)
            if nvalue != value:
              value = nvalue
          if key == 'Creation Time':
            ttime = time.strptime(value, "%a %b %d %H:%M:%S %Y")
            value = ttime
          currentsnap[key] = value

    if currentsnap:
      self.addsnap(currentsnap)

  def createsnap(self, snapname):
    """
    volume snapshot create -vserver svm_mixed_it -volume vol_wk_sfs_linux_repo -snapshot EricTest
    """
    cmd = 'volume snapshot create -vserver %s -volume %s -snapshot %s' % (self.svm.name, self.name, snapname)
    self.svm.cluster.runinteractivecmd(cmd)

  def deletesnap(self, snapname):
    """
    volume snapshot create -vserver svm_mixed_it -volume vol_wk_sfs_linux_repo -snapshot EricTest
    """
    cmd = 'volume snapshot delete -vserver %s -volume %s -snapshot %s' % (self.svm.name, self.name, snapname)
    self.svm.cluster.runinteractivecmd(cmd)



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
      output = self.cluster.runcmd(cmd, excludes=['Vserver Name', 'This operation is only supported on data Vservers',
                                                  'has type "node"', 'has type "admin"'])
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

  def findvolume(self, volume, exact=True):
    foundvolumes = []
    self.fetchvolumes()

    for nvolume in self.volumes:
      if exact:
        if nvolume == volume:
          return [self.volumes[volume]]
      else:
        if re.search(volume, nvolume):
          foundvolumes.append(self.volumes[nvolume])

    return foundvolumes

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
    self.aggregates = {}
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

  def fetchaggrs(self):
    output = self.runcmd('aggr show -instance')
    currentaggr = {}
    lastkey = None
    for line in output:
      if not line:
        lastkey = None
        continue
      if '    Aggregate:' in line and currentaggr:
        try:
          self.aggregates[currentaggr['Aggregate']]= AGGR(currentaggr['Aggregate'], self, data = currentaggr)
        except KeyError:
          print(currentaggr)
        currentaggr = {}
      if ':' in line:
        line = line.strip()
        tlist = line.split(':', 1)
        slist = [x.strip() for x in tlist]
        lastkey = slist[0]
        value = slist[1]
        if 'size' in lastkey or 'Size' in lastkey:
          nvalue = convertnetappsize(value)
          if nvalue != value:
            value = nvalue
        currentaggr[lastkey] = value
      else:
        if lastkey:
          if type(currentaggr[lastkey]) == list:
            currentaggr[lastkey].append(line.strip())
          else:
            nvalue = [currentaggr[lastkey], line.strip()]
            currentaggr[lastkey] = nvalue

    if currentaggr:
      self.aggregates[currentaggr['Aggregate']]= AGGR(currentaggr['Aggregate'], self, data = currentaggr)

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

  def findvolume(self, volume, svm=None, exact=True):
    if svm in self.svms:
      return self.svms[svm].findvolume(volume, exact)
    else:
      foundvolumes = []
      for svmo in self.svms.values():
        nvolumes = svmo.findvolume(volume, exact)
        foundvolumes.extend(nvolumes)

      return foundvolumes

  def connect(self):
    if not self.ssh.get_transport() or not self.ssh.get_transport().is_active():
      if self.pkey:
        self.ssh.connect(self.address, username=self.username, pkey=self.pkey)
      else:
        self.ssh.connect(self.address, username=self.username, password=self.pw)

  def runcmd(self, cmd, excludes=None):
    """
    run a command and returns the output
    """
    self.connect()

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

  def runinteractivecmd(self, cmd, respondto=' {y|n}:', response='y'):
    """
    run a command that requires a response, also used to see output of a command
    """
    self.connect()
    print(cmd)
    stdin, stdout, stderr = self.ssh.exec_command(cmd)
    for line in stdout:
      print(line)
      line = line.rstrip()
      if respondto in line:
        print('sending %s to server' % response)
        stdin.write(response)
        stdin.flush()


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

  def findcluster(self, cluster):
    for tcluster in self.clusters.values():
      if tcluster.name == cluster or tcluster.cname == cluster:
        return tcluster

    return None

  def findvolume(self, volume, cluster=None, svm=None, exact=True):
    if cluster:
      clustero = self.findcluster(cluster)
      if clustero:
        return clustero.findvolume(volume, svm=svm, exact=exact)
      else:
        print('Could not find cluster %s' % cluster)
        return []

    elif svm:
      svmo = self.findsvm(svm)
      if svmo:
        nvols = svmo.findvolume(volume, exact)
        if exact and len(nvols) > 0:
          return [nvols[0]]
        else:
          return nvols

      else:
        print('Could not find svm %s' % svm)
        return []

    else:
      foundvolumes = []
      for cluster in self.clusters.values():
        nvols = cluster.findvolume(volume, exact=exact)
        foundvolumes.extend(nvols)

      if exact and len(foundvolumes) > 0:
        return [foundvolumes[0]]
      else:
        return foundvolumes

    return []

  def findsvm(self, svm):
    for cluster in self.clusters.values():
      if svm in cluster.svms:
        return cluster.svms[svm]

    return None

