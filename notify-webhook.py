#!/usr/bin/env python
# 
# This is a post-receive hook that sends a POST of json and sends
# an email with the commit data.
#
# Requires simplejson
#
# based off two scripts: 
#   - https://github.com/metajack/notify-webhook
#   - https://github.com/brasse/post_receive_email.py
# Customized by Matthew Yeazel
#

import sys
import urllib, urllib2
import re
import os
import subprocess
from datetime import datetime
import simplejson as json

from email.mime.text import MIMEText
from StringIO import StringIO
from collections import defaultdict
import smtplib

MAILINGLIST = 'hooks.mailinglist'
EMAILPREFIX = 'hooks.emailprefix'
SMTP_SUBJECT = 'hooks.smtp-subject'
SMTP_HOST = 'hooks.smtp-host'
SMTP_PORT = 'hooks.smtp-port'
SMTP_SENDER = 'hooks.smtp-sender'
POST_RECEIVE_LOGFILE = 'hooks.post-receive-logfile'
PROTECTED_BRANCHES = ['production', 'development', 'testing']

class Mailer(object):
    def __init__(self, smtp_host, smtp_port,
                 sender, recipients):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.sender = sender
        self.recipients = recipients

    def send(self, subject, reply_to, message):
        if not self.recipients:
            return

        mime_text = MIMEText(message, _charset='utf-8')
        mime_text['From'] = self.sender
        mime_text['Reply-To'] = reply_to
        mime_text['To'] = ', '.join(self.recipients)
        mime_text['Subject'] = subject

        server = smtplib.SMTP(self.smtp_host, self.smtp_port)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.sendmail(self.sender, self.recipients, 
                        mime_text.as_string())
        server.rset()
        server.quit()

def git(args):
    args = ['git'] + args
    git = subprocess.Popen(args, stdout = subprocess.PIPE)
    details = git.stdout.read()
    details = details.strip()
    return details

def get_config(key):
    details = git(['config', '--get', '%s' % (key)])
    if len(details) > 0:
        return details
    else:
        return None

def get_repo_name():
    if git(['rev-parse','--is-bare-repository']) == 'true':
        name = os.path.basename(os.getcwd())
        if name.endswith('.git'):
            name = name[:-4]
        return name
    else:
        return os.path.basename(os.path.dirname(os.getcwd()))

post_string = get_config('hooks.webhookurl')
POST_URLS = [x.strip() for x in post_string.split(',')]
REPO_URL = get_config('meta.url')

COMMIT_URL = get_config('meta.commiturl')
if COMMIT_URL == None and REPO_URL != None:
    COMMIT_URL = REPO_URL + r'/commit/%s'
REPO_NAME = get_repo_name()
REPO_DESC = ""
try:
    REPO_DESC = get_config('meta.description') or open('description', 'r').read()
except Exception:
    pass
REPO_OWNER_NAME = get_config('meta.ownername')
REPO_OWNER_EMAIL = get_config('meta.owneremail')
if REPO_OWNER_NAME is None:
    REPO_OWNER_NAME = git(['log','--reverse','--format=%an']).split("\n")[0]
if REPO_OWNER_EMAIL is None:
    REPO_OWNER_EMAIL = git(['log','--reverse','--format=%ae']).split("\n")[0]

EMAIL_RE = re.compile("^(.*) <(.*)>$")

def get_revisions(old, new):
    git = subprocess.Popen(['git', 'rev-list', '--pretty=medium', '--reverse', '%s..%s' % (old, new)], stdout=subprocess.PIPE)
    sections = git.stdout.read().split('\n\n')[:-1]

    revisions = []
    s = 0
    while s < len(sections):
        lines = sections[s].split('\n')

        # first line is 'commit HASH\n'
        props = {'id': lines[0].strip().split(' ')[1]}

        # read the header
        for l in lines[1:]:
            key, val = l.split(' ', 1)
            props[key[:-1].lower()] = val.strip()

        # read the commit message
        props['message'] = sections[s+1]

        # use github time format
        basetime = datetime.strptime(props['date'][:-6], "%a %b %d %H:%M:%S %Y")
        tzstr = props['date'][-5:]
        props['date'] = basetime.strftime('%Y-%m-%dT%H:%M:%S') + tzstr

        # split up author
        m = EMAIL_RE.match(props['author'])
        if m:
            props['name'] = m.group(1)
            props['email'] = m.group(2)
        else:
            props['name'] = 'unknown'
            props['email'] = 'unknown'
        del props['author']

        revisions.append(props)
        s += 2

    return revisions

def make_json(old, new, ref):
    data = {
        'before': old,
        'after': new,
        'ref': ref,
        'repository': {
            'url': REPO_URL,
            'name': REPO_NAME,
            'description': REPO_DESC,
            'owner': {
                'name': REPO_OWNER_NAME,
                'email': REPO_OWNER_EMAIL
                }
            }
        }

    revisions = get_revisions(old, new)
    commits = []
    for r in revisions:
        url = None
        if COMMIT_URL != None:
            url = COMMIT_URL % r['id']
        commits.append({'id': r['id'],
                        'author': {'name': r['name'], 'email': r['email']},
                        'url': url,
                        'message': r['message'],
                        'timestamp': r['date']
                        })
    data['commits'] = commits

    try:
    	send_mail(old, new, ref, data, commits)
    except:
        print "Unexepected error:", sys.exc_info()[0]

    return json.dumps(data)

def get_commit_info(hash):
    p = subprocess.Popen(['git', 'show', '--pretty=format:%s%n%h', '-s', hash], 
                         stdout=subprocess.PIPE)
    s = StringIO(p.stdout.read())
    def undefined(): 
        return 'undefined'
    info = defaultdict(undefined)
    for k in ['message', 'hash']:
        info[k] = s.readline().strip()
    return info

def get_config_variables():
    def optional(variable):
        config[variable] = get_config(variable)
    def required(variable, type_=str):
        v = get_config(variable)
        if not v:
            raise RuntimeError('This script needs %s to work.' % variable)
        config[variable] = type_(v)
    def recipients(variable):
        v = get_config(variable)
        config[variable] = [r for r in re.split(' *, *| +', v) if r]

    config = {}
    optional(EMAILPREFIX)
    optional(SMTP_SUBJECT)
    required(SMTP_HOST)
    required(SMTP_PORT, int)
    required(SMTP_SENDER)
    recipients(MAILINGLIST)
    return config

def send_mail(old, new, ref, data, commits):
    config = get_config_variables()
    protected_branch = False
    full_message = "Digest of all commits follows:"
    i = 0
    for commit in commits:
        subject_template = ('[gitolite] %(name)s %(ref)s %(hash)s commit')
        info = get_commit_info(commit['id'])
        info['ref'] = data['ref'].rsplit('/')[-1]
        info['name'] = data['repository']['name']
        info['hash'] = commit['id'][0:7]
        subject = subject_template % info
        message_pre = "The branch " + info['ref'] + " has been updated\n\nOld: " + old + "\nNew: " + new + "\n\n"
        message_mid = git(['show', commit['id']])
        message_post = git(['diff-tree', '--stat', '--summary', '--find-copies-harder', old + '..' + new])
        message = message_pre + message_mid + "\n\n\nSummary of Changes\n\n" + message_post
        full_message += "\n\n" + subject + "\n" + message
        match = re.search(r'Author: (.+)', message)
        assert match
        author_email = match.group(1)
        if info['ref'] not in PROTECTED_BRANCHES:
            mailer = Mailer(config[SMTP_HOST], config[SMTP_PORT], config[SMTP_SENDER], [author_email])
            mailer.send(subject, author_email, message)
        else:
            protected_branch = True
    if protected_branch == True:
        mailer = Mailer(config[SMTP_HOST], config[SMTP_PORT], config[SMTP_SENDER], config[MAILINGLIST])
        mailer.send(subject, author_email, full_message)

def post(url, data):
    u = urllib2.urlopen(url, urllib.urlencode({'payload': data}))
    u.read()
    u.close()

if __name__ == '__main__':
    for line in sys.stdin.xreadlines():
        old, new, ref = line.strip().split(' ')
        if old == '0000000000000000000000000000000000000000':
	# This is a new branch, grab last commit and put it in there
            twocommits = git(['rev-list', '--max-count=2', '--all'])
            old = str(twocommits.split('\n')[1])
        if new == '0000000000000000000000000000000000000000':
            new = str(old[:])
        data = make_json(old, new, ref)
        if POST_URLS:
            for url in POST_URLS:
                try:
                    post(url, data)
                except:
                    print "Could not contact server: ", sys.exc_info()[0]
        else:
            print(data)
