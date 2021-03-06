#!/usr/bin/env python
#-*- coding: ISO-8859-1 -*-
"""
CMS Tag Collector interface
"""

# system modules
import os
import re

# cmssh modules
from cmssh.utils import Memoize, platform
from cmssh.cms_urls import tc_url
from cmssh.url_utils import get_data
from cmssh.regex import pat_release

def match_platform(arch):
    "Match given architecture with OS"
    if  platform() == 'darwin' or platform() == 'osx':
        if arch.find('osx') != -1:
            return True
    elif platform() == 'linux':
        if  arch.find('slc') != -1:
            return True
    return False

@Memoize(interval=3600)
def releases(rel_name=None, rfilter=None):
    "Return information about CMS releases"
    if  rel_name:
        if  not pat_release.match(rel_name):
            msg = 'Wrong CMSSW release name'
            raise ValueError(msg)
        args  = {'release_name': rel_name}
    else:
        args  = {}
    url = tc_url('getReleasesInformation')
    rel_info  = get_data(url, args)
    columns   = rel_info['columns']
    pat = re.compile('CMSSW_[1-9]_[0-9]_X\.*')
    for key, val in rel_info['data'].iteritems():
        if rfilter == 'list':
            if  pat.match(key) or not key.find('CMSSW') != -1 or \
                key.find('EXPERIMENTAL') != -1 or \
                key.find('CLANG') != -1 or \
                key.find('_X_') != -1 or \
                key.find('FORTIFIED') != -1:
                continue
        row   = {}
        pairs = zip(columns['release_name'], val)
        for kkk, vvv in pairs:
            if  isinstance(kkk, basestring):
                row[kkk] = vvv
            elif isinstance(kkk, list):
                for item in vvv:
                    row.setdefault('architectures', []).append(dict(zip(kkk, item)))
        row['release_name'] = key
        for item in row['architectures']:
            if  match_platform(item['architecture_name']):
                yield row

def architectures(arch_type='production'):
    "Return list of CMSSW known architectures"
    if  not arch_type:
        arch_type = 'all'
    prod_arch = set()
    dev_arch  = set()
    for row in releases():
        for item in row['architectures']:
            if  item['is_production_architecture']:
                prod_arch.add(item['architecture_name'])
            else:
                dev_arch.add(item['architecture_name'])
    if  arch_type == 'production':
        arch_list = list(prod_arch)
    elif arch_type == 'development':
        arch_list = list(dev_arch)
    elif arch_type == 'all':
        arch_list = list(prod_arch | dev_arch)
    else:
        raise NotImplementedError
    arch_list.sort()
    return arch_list

if __name__ == '__main__':
    for r in releases():
        print r
