#!/usr/bin/env python

"""Filemover cli equivalent"""

# system modules
import os
import re
import sys
import json
import stat
import time
import thread
import urllib
import urllib2
import datetime
from multiprocessing import Process

# for DBS2 XML parsing
import xml.etree.ElementTree as ET

# cmssh modules
from cmssh.iprint import print_error, print_info, print_warning
from cmssh.utils import size_format
from cmssh.ddict import DotDict
from cmssh.cms_urls import phedex_url, dbs_url, dbs_instances
from cmssh.cms_objects import CMSObj
from cmssh.utils import execmd
from cmssh.utils import PrintProgress, qlxml_parser
from cmssh.url_utils import get_data
from cmssh.sitedb import SiteDBManager
from cmssh.srmls import srmls_printer, srm_ls_printer

def get_dbs_se(lfn):
    "Get original SE from DBS for given LFN"
    # TODO: should have transparent access to DBS2/DBS3
    query = 'find site where file=%s' % lfn
    params = {"api":"executeQuery", "apiversion": "DBS_2_0_9", "query":query}
    default_instance = os.environ.get('DBS_INSTANCE')
    for inst in dbs_instances():
        params.update({"query":query})
        os.environ['DBS_INSTANCE'] = inst
        data  = urllib2.urlopen(dbs_url(), urllib.urlencode(params))
        try:
            rec = [f for f in qlxml_parser(data, 'site')][0]
            sename = rec['site']['site']
        except:
            continue
        os.environ['DBS_INSTANCE'] = default_instance
        return sename
    os.environ['DBS_INSTANCE'] = default_instance

def file_size(ifile):
    "Return file size"
    if  os.path.isfile(ifile):
        return os.stat(ifile)[stat.ST_SIZE]
    return 0

def check_permission(dst, verbose=None):
    """
    Check permission to write to given destination area
    """
    if  verbose:
        print "Check permission to write to %s" % dst
    srmmkdir = os.environ.get('SRM_MKDIR', '')
    if  not srmmkdir:
        print_error('Unable to find srm mkdir command')
        sys.exit(1)
    cmd    = '%s %s' % (srmmkdir, dst)
    stdout, stderr = execmd(cmd)
    if  stderr.find('command not found') != -1:
        print 'Unable to find srm mkdir tool'
        print help
        sys.exit(1)
    if  stdout.find('SRM-DIR: directory not created') != -1 or\
        stdout.find('SRM_FAILURE') != -1:
        msg = "Unable to access %s:" % dst
        print msg
        print "-" * len(msg)
        print
        print stdout
        sys.exit(1)

def check_software(softlist):
    """
    Perform the check that Grid middleware is installed on a node
    """
    help     = 'Please run with --help for more options'
    for cmd in softlist:
        stdout, stderr = execmd(cmd)
        if  not stdout:
            print 'Unable to find %s' % cmd
            print help
            sys.exit(1)

def parser(data):
    """Parser DBS2 listFiles output"""
    elem  = ET.fromstring(data)
    for i in elem:
        if  i.tag == 'file':
            yield i.attrib['lfn']

def parse_srmls(data):
    """Parse srm-ls XML output"""
    data = data.split('<?xml version="1.0" encoding="UTF-8"?>')
    data = '<?xml version="1.0" encoding="UTF-8"?>' + data[-1]
    elem  = ET.fromstring(data)
    for i in elem:
        if  i.tag == 'file' and i.attrib.has_key('size'):
            return i.attrib['size']

def lfns(run=None, dataset=None):
    """
    Get lfns list for provided run/dataset
    """
    url    = dbs_url('files') # DBS3
    params = {'detail':'True'}
    if  run:
        args['minrun'] = run
        args['maxrun'] = run
    if  dataset:
        args['dataset'] = dataset
    params.update(args)
    json_dict = get_data(url, params)
    for row in json_dict:
        yield row['logical_file_name']

def get_username(verbose=None):
    """
    Get user name from provided DN
    """
    # get DN from grid-proxy-info
    cmd    = 'grid-proxy-info'
    stdout, stderr = execmd(cmd)
    if  stderr.find('command not found') != -1:
        raise Exception(stderr)
    userdn = None
    try:
        for line in stdout.split('\n'):
            if  line.find('issuer') != -1:
                issuer, userdn = line.split(' : ')
    except:
        raise Exception('Unable to parse grid-proxy-info:\n%s' % stdout)
    if  verbose:
        print "userdn :", userdn
    if  not userdn:
        msg = 'Unable to determine your DN, please run grid-proxy-init'
        raise Exception(msg)
    mgr  = SiteDBManager()
    user = mgr.get_user(userdn)
    return user

def nodes(select=True):
    """
    Yield list of Phedex nodes, I only select T2 and below
    """
    result = get_data(phedex_url('nodes'), {})
    pat    = re.compile('^T[0-1]_[A-Z]+(_)[A-Z]+')
    lnodes = []
    for row in result['phedex']['node']:
        if  select and pat.match(row['name']):
            continue
        msg = "%s, SE: %s, description %s/%s" \
        % (row['name'], row['se'], row['technology'], row['kind'])
        lnodes.append(msg)
    lnodes.sort()
    for row in lnodes:
        print row

def resolve_srm_path(node, verbose=None):
    """
    Use TFC phedex API to resolve srm path for given node
    """
    params = {'node':node}
    result = get_data(phedex_url('tfc'), params)
    for row in result['phedex']['storage-mapping']['array']:
        if  row['protocol'] == 'srmv2' and row['element_name'] == 'lfn-to-pfn':
            yield (row['result'], row['path-match'])

def resolve_user_srm_path(node, ldir='/store/user', verbose=None):
    """
    Use TFC phedex API to resolve srm path for given node
    """
    # change ldir if user supplied full path, e.g. /xrootdfs/cms/store/...
    ldir   = '/store/' + ldir.split('/store/')[-1]
    params = {'node':node, 'lfn':ldir, 'protocol': 'srmv2'}
    result = get_data(phedex_url('lfn2pfn'), params)
    for row in result['phedex']['mapping']:
        yield row['pfn']

def lfn2pfn(lfn, sename, mgr=None):
    "Find PFN for given LFN and SE"
    pfnlist = []
    if  not mgr:
        mgr = SiteDBManager()
    cmsname = mgr.get_name(sename)
    if  cmsname:
        params = {'protocol':'srmv2', 'lfn':lfn, 'node':cmsname}
        result = get_data(phedex_url('lfn2pfn'), params)
        try:
            for item in result['phedex']['mapping']:
                pfn = item['pfn']
                if  pfn not in pfnlist:
                    pfnlist.append(pfn)
        except:
            msg = "Fail to look-up PFNs in Phedex\n" + str(result)
            print msg
    return pfnlist

def get_pfns(lfn, verbose=None):
    """
    Look-up LFN in Phedex and get corresponding list of PFNs
    """
    pfnlist   = []
    selist    = []
    params    = {'se':'*', 'lfn':lfn}
    json_dict = get_data(phedex_url('fileReplicas'), params)
    ddict     = DotDict(json_dict)
    if  not json_dict['phedex']['block']:
        return pfnlist, selist
    for fname in ddict.get('phedex.block.file'):
        for replica in fname['replica']:
            cmsname = replica['node']
            se      = replica['se']
            if  se not in selist:
                selist.append(se)
            # query Phedex for PFN
            params = {'protocol':'srmv2', 'lfn':lfn, 'node':cmsname}
            result = get_data(phedex_url('lfn2pfn'), params)
            try:
                for item in result['phedex']['mapping']:
                    pfn = item['pfn']
                    if  pfn not in pfnlist:
                        pfnlist.append(pfn)
            except:
                msg = "Fail to look-up PFNs in Phedex\n" + str(result)
                print msg
                continue
    return pfnlist, selist

def pfn_dst(lfn, dst, verbose=None):
    """
    Look-up LFN in Phedex and return pfn dst for further processing
    """
    dstfname = None
    pat = re.compile('^T[0-9]_[A-Z]+(_)[A-Z]+')
    if  pat.match(dst):
        dst_split = dst.split(':')
        dst = dst_split[0]
        if  len(dst_split) == 1: # copy to the node
            local_path = dst_split[1]
            for srm_path, lfn_match in resolve_srm_path(dst, verbose):
                lfn_pat = re.compile(lfn_match)
                if  lfn_pat.match(lfn):
                    srm_path = srm_path.replace('\?', '?').replace('$1', local_path)
                    if  verbose:
                        print "Resolve %s into %s" % (dst, srm_path)
                    dst = srm_path
        else:
            paths = [p for p in resolve_user_srm_path(dst, verbose=verbose)]
            dst = '%s/%s' % (paths[0], get_username())
        check_permission(dst, verbose)
    else:
        if  dst.find('file:///') == -1:
            dstfname = dst.split('/')[-1]
            if  dstfname == '.':
                dstfname = None
            if  dst[0] == '/': # absolute path
                if  os.path.isdir(dst):
                    ddir = dst
                    dstfname = None
                else:
                    ddir =  '/'.join(dst.split('/')[:-1])
                if  not os.path.isdir(ddir):
                    msg = 'Provided destination directory %s does not exists' % ddir
                    raise Exception(msg)
                dst = 'file:///%s' % ddir
            else:
                ddir = '/'.join(dst.split('/')[:-1]).replace('$PWD', os.getcwd())
                if  os.path.isdir(ddir):
                    dst = 'file:///%s' % os.path.join(os.getcwd(), ddir)
                else:
                    dst = 'file:///%s' % os.getcwd()
    pfnlist   = []
    if  os.path.isfile(lfn) or lfn.find('file:///') != -1: # local file
        pfn = lfn.replace('file:///', '')
        if  pfn[0] != '/':
            pfn = 'file:///%s' % os.path.join(os.getcwd(), pfn)
        else:
            pfn = 'file:///%s' % pfn
        pfnlist   = [pfn]
    else:
        if  lfn.find(':') != -1:
            node, lfn = lfn.split(':')
            params    = {'node':node, 'lfn':lfn, 'protocol':'srmv2'}
            method    = 'lfn2pfn'
        else:
            params    = {'se':'*', 'lfn':lfn}
            method    = 'fileReplicas'
        json_dict = get_data(phedex_url(method), params)
        ddict     = DotDict(json_dict)
        if  verbose:
            print "Look-up LFN:"
            print lfn
        phedex = json_dict['phedex']
        if  phedex.has_key('mapping'):
            if  not phedex['mapping']:
                msg  = "LFN: %s\n" % lfn
                msg += 'No replicas found\n'
                msg += str(json_dict)
                raise Exception(msg)
            filelist = ddict.get('phedex.mapping.pfn')
            if  not filelist:
                filelist = []
            if  isinstance(filelist, basestring):
                filelist = [filelist]
            for fname in filelist:
                pfnlist.append(fname)
        elif  phedex.has_key('block') and not phedex['block']:
            msg = 'No replicas found in PhEDEx, will try to get original SE from DBS'
            print_warning(msg)
            sename = get_dbs_se(lfn)
            msg = 'Orignal LFN site %s' % sename
            print_info(msg)
            mgr = SiteDBManager()
            pfnlist = lfn2pfn(lfn, sename, mgr)
        filelist = ddict.get('phedex.block.file')
        if  not filelist:
            filelist = []
        for fname in filelist:
            for replica in fname['replica']:
                cmsname = replica['node']
                se      = replica['se']
                if  verbose:
                    print "found LFN on node=%s, se=%s" % (cmsname, se)
                if  cmsname.count('T0', 0, 2) == 1:
                    continue # skip T0's
                # query Phedex for PFN
                params = {'protocol':'srmv2', 'lfn':lfn, 'node':cmsname}
                result = get_data(phedex_url('lfn2pfn'), params)
                try:
                    for item in result['phedex']['mapping']:
                        pfn = item['pfn']
                        if  pfn not in pfnlist:
                            pfnlist.append(pfn)
                except:
                    msg = "Fail to look-up PFNs in Phedex\n" + str(result)
                    print msg
                    continue
    if  verbose > 1:
        print "PFN list:"
        for pfn in pfnlist:
            print pfn

    # finally return pfn and dst paths w/ file for further processing
    for item in pfnlist:
        ifile = item.split("/")[-1] if not dstfname else dstfname
        yield item, '%s/%s' % (dst, ifile)

def get_size(surl, verbose=None):
    """
    Execute srm-ls <surl> command and retrieve file size information
    """
    srmls = os.environ.get('SRM_LS', '')
    if  not srmls:
        print_error('Unable to find srm ls tool')
        sys.exit(1)
    if  srmls.find('srm-ls') != -1:
        srmargs = ''
    else:
        srmargs = '-2'
    cmd = '%s %s %s' % (srmls, srmargs, surl)
    if  verbose:
        print_info(cmd)
    if  cmd.find('file:///') != -1:
        return file_size(cmd.split('file:///')[-1])
    stdout, stderr = execmd(cmd)
    if  verbose:
        print_info(stdout + stderr)
    orig_size = 0
    if  cmd.find('file:///') != -1: # srm-ls returns XML
        if  srmls.find('srm-ls') != -1:
            orig_size = parse_srmls(stdout)
        else:
            try:
                orig_size = stdout.split()[0].strip()
            except:
                return 0
    else:
        if  srmls.find('srm-ls') != -1:
            for line in stdout.split('\n'):
                if  line.find('Bytes') != -1:
                    orig_size = line.replace('\n', '').split('=')[-1]
        else:
            try:
                orig_size = stdout.split()[0].strip()
            except:
                return 0
    return orig_size

def check_file(src, dst, verbose):
    """
    Check if file is transfered and return dst, dst_size upon success.
    """
    # find file size from replica
    orig_size = get_size(src, verbose)
    if  verbose:
        print "%s, size %s" % (src, orig_size)

    if  not orig_size or orig_size == 'null':
        return False

    # find file size from destination (if any)
    dst_size = get_size(dst, verbose)
    if  verbose:
        print "%s, size %s" % (dst, dst_size)

    if  not dst_size or dst_size == 'null':
        return False

    if  int(orig_size) == int(dst_size):
        return (dst, int(dst_size))
    return False

def execute(cmds, src, dst, verbose):
    """
    Execute given command, but also check if file is in place at dst
    """
    status = check_file(src, dst, verbose)
    if  status:
        return status
    else:
        if  isinstance(cmds, basestring):
            stdout, stderr = execmd(cmds)
            if  verbose:
                print_info('Output of %s' % cmd)
                print stdout + stderr
            status = check_file(src, dst, verbose)
        elif isinstance(cmds, list):
            for cmd in cmds:
                if  not cmd:
                    continue
                stdout, stderr = execmd(cmd)
                if  verbose:
                    print_info('Output of %s' % cmd)
                    print stdout + stderr
                status = check_file(src, dst, verbose)
                if  status:
                    return status
    return status

def active_jobs(queue):
    "Return number of active jobs in a queue"
    njobs = 0
    for _, (proc, _status) in queue.items():
        if  proc.is_alive():
            njobs += 1
    return njobs

def worker(queue, threshold):
    """
    Worker which start processes in a queue and monitor that number of
    jobs does not exceed a given threshold
    """
    while True:
        njobs = active_jobs(queue)
        if  njobs < threshold:
            # start process
            for lfn, (proc, status) in queue.items():
                if  active_jobs(queue) >= threshold:
                    break
                if  not status and not proc.is_alive():
                    proc.start()
                    queue[lfn] = (proc, 'started')
        time.sleep(5)

class FileMover(object):
    def __init__(self):
        self.instance = "Instance at %d" % self.__hash__()
        self.queue = {} # download queue
        threshold = os.environ.get('CMSSH_TRANSFER_LIMIT', 3)
        thread.start_new_thread(worker, (self.queue, threshold))
        self.methods = ['xrdcp', 'lcgcp', 'srmcp']

    def transfer_cmds(self, lfn, dst, verbose=0):
        "Generate transfer commands"
        xrdcmd = 'xrdcp root://cms-xrd-global.cern.ch/%s %s' % (lfn, dst)
        if  not os.path.isdir(dst):
            xrdcmd = ''
        srmcp  = os.environ.get('SRM_CP', '')
        if  srmcp.find('srm-copy') != -1:
            srmargs = '-pushmode -statuswaittime 30 -3partycopy -delegation false -dcau false'
        else:
            srmargs = '-srm_protocol_version=2 -retry_num=1 -streams_num=1 -debug'
        for pfn, pdst in pfn_dst(lfn, dst, 0): # last zero is verbose=0
            lcg = os.environ.get('LCG_CP', '')
            if  lcg:
                if  verbose:
                    vflag = '-v'
                else:
                    vflag = ''
                lcgcmd = '%s %s -b -D srmv2 %s %s' % (lcg, vflag, pfn, pdst)
            else:
                lcgcmd = ''
            if  srmcp.find('srm-copy') != -1:
                srmcmd = '%s %s %s %s' % (srmcp, pfn, pdst, srmargs)
            else:
                srmcmd = '%s %s %s %s' % (srmcp, srmargs, pfn, pdst)
            yield xrdcmd, lcgcmd, srmcmd, pfn, pdst

    def copy(self, lfn, dst, method='xrdcp', verbose=0, background=False):
        """Copy LFN to given destination"""
        if  method not in self.methods:
            print_error('Unknown transfer method "%s"' % method)
            return 'fail'
        for xrdcmd, lcgcmd, srmcmd, pfn, pdst in self.transfer_cmds(lfn, dst, verbose):
            if  method == 'xrdcp':
                cmd = xrdcmd
            elif method == 'lcgcp':
                cmd = lcgcmd
            else:
                cmd = srmcmd
            if  not cmd:
                return 'fail'
            if  background:
                # I need to pass list of commands for transfer method
                # for that I'll use background variable
                background = [xrdcmd, lcgcmd, srmcmd]
            status = self.transfer(cmd, lfn, pfn, pdst, verbose, background)
            if  status == 'success' or status == 'accepted':
                return status
        return 'fail'

    def transfer(self, cmd, lfn, pfn, pdst, verbose=0, background=False):
        """Copy LFN to given destination"""
        err  = 'Unable to identify total size of the file,'
        err += ' GRID middleware fails.'
        if  not background:
            bar  = PrintProgress('Fetching LFN info')
        if  verbose:
            print_info(cmd)
        if  background:
            # here background is a list of commands
            if  not isinstance(background, list):
                return 'fail'
            proc = Process(target=execute, args=(background, pfn, pdst, 0))
            self.queue[lfn] = (proc, None)
            return 'accepted'
        elif verbose:
            status = execute(cmd, pfn, pdst, verbose)
            if  not status:
                return 'fail'
            else:
                dst, dst_size = status
                size = size_format(dst_size)
                if  not size or not dst_size:
                    print_error(err)
                    print "Status of transfer:\n", status
                    return 'fail'
                else:
                    print "\nDone, file located at %s (%s)" \
                        % (dst, size_format(dst_size))
                return 'success'
        else:
            ifile = pdst
            pfn_size = get_size(pfn)
            if  pfn_size and pfn_size != 'null':
                tot_size = float(pfn_size)
                bar.print_msg('LFN size=%s' % size_format(tot_size))
                bar.init('Download in progress:')
                proc = Process(target=execute, args=(cmd, pfn, pdst, verbose))
                proc.start()
                while True:
                    if  proc.is_alive():
                        size = get_size(ifile)
                        if  not size or size == 'null':
                            bar.refresh('')
                            pass
                        else:
                            progress = float(size)*100/tot_size
                            bar.refresh(progress)
                            if  progress == 100:
                                break
                    else:
                        break
                    time.sleep(0.5)
                bar.clear()
                status = check_file(pfn, pdst, verbose)
                if  status:
                    return 'success'
            else:
                print_error(err)
                return 'fail'
        return 'fail'

    def list_lfn(self, lfn, verbose=0):
        """List LFN"""
        pat_lfn = re.compile('^/.*\.root$')
        if  pat_lfn.match(lfn):
            pfnlist, selist = get_pfns(arg, verbose)
            for pfn in pfnlist:
                print '%s %s' % (lfn, get_size(pfn, verbose))

    def list_se(self, arg, verbose=0):
        """list content of given directory on SE"""
        try:
            node, ldir = arg.split(':')
        except:
            msg = 'Given argument "%s" does not represent SE:dir' % arg
            raise Exception(msg)
        srmls = os.environ.get('SRM_LS', '')
        if  not srmls:
            print_error('Unable to find srm ls tool')
            sys.exit(1)
        dst = [r for r in resolve_user_srm_path(node, ldir)][0]
        if  os.environ.get('LCG_LS', ''):
            cmd = "%s -l -v -b -D srmv2 %s" % (os.environ['LCG_LS'], dst)
        else:
            if  srmls.find('srm-ls') != -1:
                cmd = "%s %s -fulldetailed" % (srmls, dst)
            else:
                cmd = "%s -2 -l %s" % (srmls, dst)
        if  verbose:
            print cmd
        stdout, stderr = execmd(cmd)
        if  stderr:
            print_error(stderr)
        output = []
        row = {}
        if  os.environ.get('LCG_LS', ''):
            for line in stdout.split('\n'):
                if  line.find('SE type') != -1:
                    continue
                output.append(line)
            return '\n'.join(output)
        elif srmls.find('srmls') != -1:
            for line in srmls_printer(stdout, dst.split('=')[-1]):
                output.append(line)
            return '\n'.join(output)
        else:
            for line in srm_ls_printer(stdout, dst.split('=')[-1]):
                output.append(line)
            return '\n'.join(output)

    def rm_lfn(self, arg, verbose=0):
        """Remove user lfn from a node"""
        try:
            node, lfn = arg.split(':')
        except:
            msg = 'Given argument "%s" does not represent SE:LFN' % arg
            raise Exception(msg)
        cmd = os.environ.get('SRM_RM', '')
        dst = [r for r in resolve_user_srm_path(node)][0]
        dst, path = dst.split('=')
        if  dst[-1] != '=':
            dst += '='
        for item in lfn.split('/'):
            if  not item or item in path:
                continue
            path += '/%s' % item
        cmd = "%s %s" % (cmd, dst+path)
        if  verbose:
            print cmd
        try:
            stdout, stderr = execmd(cmd)
            if  verbose:
                print_info(stdout + stderr)
        except:
            return 'fail'
        return 'success'

    def rmdir(self, path, verbose=0):
        """rmdir command"""
        spath = path.split(':')
        if  len(spath) == 1:
            node = spath[0]
            ldir = '/store/user'
        else:
            node = spath[0]
            ldir = spath[1]
        dst = [r for r in resolve_user_srm_path(node, ldir)][0]
        cmd = '%s %s' % (os.environ.get('SRM_RMDIR', ''), dst)
        if  verbose:
            print_info(cmd)
        try:
            stdout, stderr = execmd(cmd)
            if  verbose:
                print_info(stdout + stderr)
        except:
            return 'fail'
        return 'success'

    def mkdir(self, path, verbose=0):
        """mkdir command"""
        spath = path.split(':')
        if  len(spath) == 1:
            node = spath[0]
            ldir = '/store/user'
        else:
            node = spath[0]
            ldir = spath[1]
        dst = [r for r in resolve_user_srm_path(node, ldir)][0]
        cmd = '%s %s' % (os.environ.get('SRM_MKDIR', ''), dst)
        if  verbose:
            print_info(cmd)
        try:
            stdout, stderr = execmd(cmd)
            if  verbose:
                print_info(stdout + stderr)
        except:
            return 'fail'
        return 'success'

def lfn_exists(lfn, dst):
    "Check if given LFN exists at local destination"
    if  dst[0] == '/' or dst[0] == '.':
        fname = lfn.split('/')[-1]
        if  os.path.isdir(dst):
            if  os.path.exists(os.path.join(dst, fname)):
                return True
        if  os.path.exists(dst):
            return True
    return False

FM_SINGLETON = FileMover()
def copy_lfn(lfn, dst, verbose=0, background=False, overwrite=False):
    """Copy lfn to destination"""
    if  overwrite:
        if  os.path.isfile(dst):
            os.remove(dst)
        if  lfn_exists(lfn, dst):
            if  os.path.isdir(dst):
                fname = lfn.split('/')[-1]
                if  os.path.exists(os.path.join(dst, fname)):
                    os.remove(os.path.join(dst, fname))
    else:
        if  lfn_exists(lfn, dst):
            if  os.path.isdir(dst):
                fname = os.path.join(dst, lfn.split('/')[-1])
                if  not os.path.exists(fname):
                    fname = None
            elif os.path.isfile(dst) and os.path.exists(dst):
                fname = dst
            else:
                fname = None
                print_warning('Destination %s is not local disk')
            if  fname:
                print_warning('File %s already exists' % fname)
                return 'fail'
    method = os.environ.get('CMSSH_TRANSFER_METHOD', 'xrdcp')
    status = FM_SINGLETON.copy(lfn, dst, method, verbose, background)
    if  status == 'fail':
        print_warning('xrdcp fails to copy file, fallback to GRID middleware mechanism')
        if  os.environ.get('LCG_CP', ''):
            status = FM_SINGLETON.copy(lfn, dst, 'lcgcp', verbose, background)
        else:
            status = FM_SINGLETON.copy(lfn, dst, 'srmcp', verbose, background)
    return status

def dqueue(arg=None):
    """Return download queue"""
    download_queue = FM_SINGLETON.queue
    alive   = []
    waiting = []
    ended   = []
    for lfn, (proc, status) in download_queue.items():
        if not status:
            waiting.append(lfn)
        elif  proc.is_alive():
            alive.append(lfn)
        else:
            ended.append((lfn, proc.exitcode))
            del download_queue[lfn]
    print "In progress: %s jobs" % len(alive)
    if  arg and arg == 'list':
        for lfn in alive:
            print lfn
        if  len(alive): print
    print "Waiting    : %s jobs" % len(waiting)
    if  arg and arg == 'list':
        for lfn in waiting:
            print lfn
        if  len(waiting): print
    print "Finished   : %s jobs" % len(ended)
    if  arg and arg == 'list':
        for lfn, code in ended:
            print "%s, exit code %s" % (lfn, code)

def list_lfn(lfn, verbose=0):
    """List lfn info"""
    return FM_SINGLETON.list_lfn(lfn, verbose)

def list_se(arg, verbose=0):
    """List SE content"""
    return FM_SINGLETON.list_se(arg, verbose)

def rm_lfn(lfn, verbose=0):
    """Remove lfn from destination"""
    return FM_SINGLETON.rm_lfn(lfn, verbose)

def mkdir(dst, verbose=0):
    """mkdir command"""
    return FM_SINGLETON.mkdir(dst, verbose)

def rmdir(dst, verbose=0):
    """rmdir command"""
    return FM_SINGLETON.rmdir(dst, verbose)
