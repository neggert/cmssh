#!/usr/bin/env python
#-*- coding: ISO-8859-1 -*-

"""
URL utils
"""

# system modules
import os
import json
import urllib
import urllib2
import httplib
import cookielib
import subprocess
from contextlib import contextmanager

# cmssh modules
from   cmssh.iprint import print_info

class HTTPSClientAuthHandler(urllib2.HTTPSHandler):
    """
    Simple HTTPS client authentication class based on provided 
    key/ca information
    """
    def __init__(self, ckey=None, cert=None):
        if  int(os.environ.get('HTTPDEBUG', 0)):
            urllib2.HTTPSHandler.__init__(self, debuglevel=1)
        else:
            urllib2.HTTPSHandler.__init__(self)
        if  ckey != cert:
            self.ckey = ckey
            self.cert = cert
        else:
            self.cert = cert
            self.ckey = None

    def https_open(self, req):
        """Open request method"""
        #Rather than pass in a reference to a connection class, we pass in
        # a reference to a function which, for all intents and purposes,
        # will behave as a constructor
        return self.do_open(self.get_connection, req)

    def get_connection(self, host, timeout=300):
        """Connection method"""
        if  self.cert:
            return httplib.HTTPSConnection(host, key_file=self.ckey,
                                                cert_file=self.cert)
        return httplib.HTTPSConnection(host)

def create_ssh_opener(key, cert):
    "Create HTTPS url opener with cookie support"
    cookie_jar = cookielib.CookieJar()
    cookie_handler = urllib2.HTTPCookieProcessor(cookie_jar)
    https_handler  = HTTPSClientAuthHandler(key, cert)
    opener = urllib2.build_opener(cookie_handler, https_handler)
    agent = 'Mozilla/5.0 (Macintosh; U; Intel Mac OS X 10.6; en-US; rv:1.9.2.11) Gecko/20101012 Firefox/3.6.11'
    opener.addheaders = [('User-Agent', agent)]
    urllib2.install_opener(opener)
    return opener

def get_data(url, method, kwargs=None, headers=None, verbose=None, decoder='json', post=False):
    """Retrieve data"""
    if  url.find('https') != -1:
        ckey, cert = get_key_cert()
    else:
        ckey = None
        cert = None
    url = os.path.join(url, method)
    if  kwargs:
        params = kwargs
    else:
        params = {}
    if  method == 'datasets':
        params.update({'dataset_access_type':'PRODUCTION', 'detail':'True'})
    encoded_data = urllib.urlencode(params, doseq=True)
    if  not post:
        url = url + '?' + encoded_data
    if  verbose:
        print "Request:", url, encoded_data, headers, ckey, cert
    req = urllib2.Request(url)
    if  headers:
        for key, val in headers.items():
            req.add_header(key, val)
    else:
        headers = {'Accept':'application/json;text/json'}
    if  cert:
        handler = HTTPSClientAuthHandler(ckey, cert)
        opener  = urllib2.build_opener(handler)
        urllib2.install_opener(opener)
    if  post:
        print "POST", req, url, encoded_data, params
#        res = urllib2.urlopen(req, encoded_data)
        res = urllib2.urlopen(req, json.dumps(params))
    else:
        res = urllib2.urlopen(req)
    if  decoder == 'json':
        data = json.load(res)
    else:
        data = res.read()
    return data

def get_key_cert():
    """
    Get user key/certificate
    """
    key  = None
    cert = None

    # Read user certificate chain from user globus area
    globus_key  = os.path.join(os.environ['HOME'], '.globus/userkey.pem')
    globus_cert = os.path.join(os.environ['HOME'], '.globus/usercert.pem')
    if  os.path.isfile(globus_key):
        key  = globus_key
    if  os.path.isfile(globus_cert):
        cert  = globus_cert

    # look for cert at default location /tmp/x509up_u$uid
    if not key or not cert:
        uid  = os.getuid()
        cert = '/tmp/x509up_u'+str(uid)
        key  = cert

    # Second preference to User Proxy, very common
    elif os.environ.has_key('X509_USER_PROXY'):
        cert = os.environ['X509_USER_PROXY']
        key  = cert

    # Third preference to User Cert/Proxy combinition
    elif os.environ.has_key('X509_USER_CERT'):
        cert = os.environ['X509_USER_CERT']
        key  = os.environ['X509_USER_KEY']

    if  not os.path.exists(cert):
        raise Exception("Certificate PEM file %s not found" % key)
    if  not os.path.exists(key):
        raise Exception("Key PEM file %s not found" % key)

    if  key == cert: # key/cert in one file, e.g. /tmp/x509up_u<uid>
        key = None   # to handle correctly HTTPSHandler call

    return key, cert

def send_email(to_user, from_user, title, ticket):
    # we will use mail unix command for that
    cmd = 'echo "User: %s\nTicket:\n%s" | mail -s "cmssh gist %s" %s'\
        % (from_user, ticket, title, to_user)
    subprocess.call(cmd, shell=True)

@contextmanager
def get_data_and_close(url, headers={'Accept':'*/*'}):
    "Context Manager to read data from given URL"
    ckey, cert = get_key_cert()
    req = urllib2.Request(url)
    if  headers:
        for key, val in headers.items():
            req.add_header(key, val)

    handler = HTTPSClientAuthHandler(ckey, cert)
    opener  = urllib2.build_opener(handler)
    urllib2.install_opener(opener)
    data    = urllib2.urlopen(req)
    try:
        yield data
    finally:
        data.close()
