#!/usr/bin/env python
# started from
# https://github.com/razius/flask-github-webhook/blob/master/index.py
import io
import os
import re
import sys
import json
import re
import subprocess
import requests
import ipaddress
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.generator import Generator
import email.charset
from cStringIO import StringIO

email.charset.add_charset('utf-8', email.charset.QP, email.charset.QP, 'utf-8')

class InvalidConfiguration(Exception):
    pass

def validate_repos(config):
    # TODO: Check that all configured repos have events with matching templates?
    # that they all have an email.to field?
    mls = json.loads(io.open(config['mls'], 'r').read())
    import os.path
    for (ml, repos) in mls.iteritems():
        for (repo,data) in repos.iteritems():
            for e in data["events"]:
                generic_template = config['TEMPLATES_DIR'] + '/generic/' + e
                ml_template = config['TEMPLATES_DIR'] + '/mls/' + ml + '/' + e
                specific_template = config['TEMPLATES_DIR'] + '/mls/' + ml + '/' + repo + '/' + e
                if not (os.path.isfile(generic_template) or os.path.isfile(ml_template)
                        or os.path.isfile(specific_template)):
                    raise InvalidConfiguration("No template matching event %s defined in %s in %s (looked at %s and %s)" % (e, config['repos'], repo, generic_template, specific_template))

def event_id(event, payload):
    if event.split(".")[0] == "issues":
        return payload["issue"]["id"]
    elif event.split(".")[0] == "issue_comment":
        return payload["comment"]["id"]
    elif event == "push":
        return payload["head_commit"]["id"]
    elif event.split(".")[0] == "pull_request":
        return payload["pull_request"]["id"]

def event_timestamp(event, payload):
    def timestamp(date):
        from dateutil import parser
        import calendar
        try:
            return calendar.timegm(parser.parse(date).utctimetuple())
        except:
            return date
    ts = None
    if event == "push":
        ts = payload["repository"]["pushed_at"]
    elif event == "issue_comment.created":
        ts = payload["comment"]["created_at"]
    elif event.split(".")[0] in ["issues", "pull_request"]:
        action = event.split(".")[1]
        key = "pull_request" if event.split(".")[0] == "pull_request" and payload.has_key("pull_request") else "issue"
        if action == "opened":
            ts = payload[key]["created_at"]
        elif action == "closed":
            ts = payload[key]["closed_at"]
        elif action == "reopened" or action == "synchronize":
            ts = payload[key]["updated_at"]
    if ts:
        return timestamp(ts)

def refevent(event, payload, target, oauth_token):
    if target=="issue" and event in ["issues.reopened", "issues.closed", "issue_comment.created"]:
        return ("issues.opened", payload["issue"]["id"])
    elif target=="pull_request" and event in ["pull_request.closed", "pull_request.reopened",
                   "pull_request.synchronized",
                                              "pull_request_review_comment.created"]:
        return ("pull_request.opened", payload["pull_request"]["id"])
    elif target == "pull_request" and event == "issue_comment.created":
        if oauth_token:
            headers = {}
            headers['Authorization']="token %s" % (oauth_token)
            pr_id = requests.get(payload['issue']['pull_request']['url'],
                                     headers=headers).json()['id']
            if pr_id:
                return ("pull_request.opened", pr_id)
    return (None,None)


def serveRequest(config, postbody):
    request_method = os.environ.get('REQUEST_METHOD', "GET")
    if request_method == 'GET':
        output = "Content-Type: text/plain; charset=utf-8\n\n"
        output += " Nothing to see here, move along ..."
        return output
    if request_method != 'POST':
        return
    if os.environ.has_key('HTTP_X_GITHUB_EVENT'):
        return githubRequest(config, postbody)
    elif os.environ.has_key('HTTP_X_W3C_WEBHOOK'):
        return w3cRequest(config, postbody)

def w3cRequest(config, postbody):
    mls = json.loads(io.open(config['mls'], 'r').read())

    payload = json.loads(postbody)
    event = payload["event"]

    def trimTrailingSlash(s):
        import re
        return re.sub(r'/$', '', s)

    trs = {}
    tr_prefix = "https://www.w3.org/TR/"
    for (ml, mltr) in mls.iteritems():
        for (url, conf) in mltr.iteritems():
            if (url[0:len(tr_prefix)] == tr_prefix):
                url = trimTrailingSlash(url)
                conf["email"] = {"to": ml}
                if (event in conf["events"]):
                    if not trs.has_key("url"):
                        trs[url] = []
                    trs[url].append(conf)
    target = trimTrailingSlash(payload["specversion"]["shortlink"])
    sentMail = []
    errors = []
    for conf in trs.get(target, []):
        to = conf["email"].get("to").split(",")
        template, error = loadTemplate(event, config["TEMPLATES_DIR"], '/mls/' + ml + '/')
        if not template:
            errors.append(error)
            continue
        from_addr = conf.get("email", {}).get("from", config["EMAIL_FROM"])
        body, subject = mailFromTemplate(template, payload["specversion"])
        sentMail.append(sendMail(config["SMTP_HOST"], body, from_addr, "W3C Webmaster via W3C API", to, subject))
    return reportSentMail(sentMail, errors)

def githubRequest(config, postbody):
    remote_addr = os.environ.get('HTTP_X_FORWARDED_FOR', os.environ.get('REMOTE_ADDR'))

    # Store the IP address blocks that github uses for hook requests.
    hook_blocks = requests.get('https://api.github.com/meta').json()['hooks']
    output = ""

    # Check if the request is from github.com
    for block in hook_blocks:
        ip = ipaddress.ip_address(u'%s' % remote_addr)
        if ipaddress.ip_address(ip) in ipaddress.ip_network(block):
            break #the remote_addr is within the network range of github
    else:
        output += "Status: 403 Unrecognized IP\n"
        output += "Content-Type: application/json\n\n"
        output += json.dumps({'msg': 'Unrecognized IP address', 'ip': remote_addr})
        return output

    event = os.environ.get('HTTP_X_GITHUB_EVENT', None)
    if event == "ping":
        output += "Content-Type: application/json\n\n"
        output += json.dumps({'msg': 'Hi!'})
        return output
    mls = json.loads(io.open(config['mls'], 'r').read())
    for (ml, mlrepos) in mls.iteritems():
        for (reponame, repoconf) in mlrepos.iteritems():
            repoconf["email"] = {"to":ml}
    payload = json.loads(postbody)
    repo_meta = {
	    'name': payload['repository'].get('name')
	    }
    repo_meta['owner'] = payload['repository']['owner'].get('name', payload['repository']['owner'].get('login'))
    match = re.match(r"refs/heads/(?P<branch>.*)", payload.get('ref', ''))
    if match:
        repo_meta['branch'] = match.groupdict()['branch']

    formatedRepoName = "{owner}/{name}".format(**repo_meta)

    def repoMatch(reponame):
        if (reponame.startswith("regexp:")):
            regexp = reponame[len("regexp:"):]
            try:
                return re.match(regexp, formatedRepoName) != None
            except:
                return False
        else:
            return reponame == formatedRepoName

    sentMail = []
    errors = []

    if payload.has_key("action"):
        event = event + "." + payload['action']

    for ml,repos in mls.iteritems():
        for reponame in filter(repoMatch, repos.keys()):
            tr_prefix = "https://www.w3.org/TR/"
            if reponame[0:len(tr_prefix)] == tr_prefix:
                continue
            repo = repos[reponame]

            if event not in repo['events'] and (not repo_meta.has_key("branch") or event not in repo.get('branches', {}).get(repo_meta['branch'], [])):
                continue
            if repo.has_key("eventFilter"):
                labelTarget = payload.get("issue", payload.get("pull_request", {})).get("labels", [])
                labelFilter = lambda x: x.get("name") == repo["eventFilter"]["label"]
                if repo["eventFilter"]["label"]:
                    if not labelFilter(payload.get("label", {})) and len(filter(labelFilter, labelTarget)) == 0:
                        continue

            template, error = loadTemplate(event, config["TEMPLATES_DIR"], '/mls/' + ml + '/', formatedRepoName)
            if not template:
                errors.append(error)
                continue
            body, subject = mailFromTemplate(template, payload)
            frum = repo.get("email", {}).get("from", config["EMAIL_FROM"])
            msgid = "<%s-%s-%s-%s>" % (event, event_id(event, payload),
                                       event_timestamp(event, payload), frum)
            target = "pull_request" if payload.has_key("pull_request") or payload.get("issue", {}).has_key("pull_request") else "issue"
            (ref_event, ref_id) = refevent(event, payload, target, config.get("GH_OAUTH_TOKEN", False))
            inreplyto = None
            if ref_event and ref_id:
                inreplyto = "<%s-%s-%s-%s>" % (ref_event, ref_id,
                                            event_timestamp(ref_event, payload),
                                            frum)

            too = repo.get("email", {}).get("to").split(",")
            headers = {}
            frum_name = ""

            if config.get("GH_OAUTH_TOKEN", False):
                headers['Authorization']="token %s" % (config["GH_OAUTH_TOKEN"])
                frum_name = requests.get(payload['sender']['url'],
                                     headers=headers
                                     ).json()['name']
                if frum_name == None:
                    frum_name = payload['sender']['login']
                frum_name = '%s via GitHub' % (frum_name)
            sentMail.append(sendMail(config["SMTP_HOST"], body, frum, frum_name, too, subject, msgid, inreplyto))
    return reportSentMail(sentMail, errors)

def reportSentMail(sentMail, errors):
    if sentMail:
        output = "Content-Type: application/json\n\n"
        output += json.dumps({'sent': sentMail, 'errors': errors})
        return output
    elif (len(errors)):
        output = "Status: 500 Error processing the request\n"
        output += "Content-Type: application/json\n\n"
        output += json.dumps({'errors': errors})
        return output
    else:
        output = "Content-Type: application/json\n\n"
        output += '"nothing done"'
        return output


def loadTemplate(name, rootpath, specificpath, optionalpath = ""):
    error = None
    template = None
    try:
        template = io.open(rootpath + specificpath + optionalpath + "/%s" % name).read()
    except IOError:
        try:
            template = io.open(rootpath + specificpath + "/%s" % name).read()
        except IOError:
            try:
                template = io.open(rootpath + "/generic/%s" % name).read()
            except IOError:
                error = {'msg': 'no template defined for event %s' % name}
    return template, error


def mailFromTemplate(template, payload):
    import pystache
    import textwrap
    body = pystache.render(template, payload)
    subject, dummy, body = body.partition('\n')
    paragraphs = body.splitlines()
    wrapper = textwrap.TextWrapper( break_long_words=False, break_on_hyphens=False,  drop_whitespace=False)
    body = "\n".join(map(wrapper.fill, paragraphs))
    return body, subject

def sendMail(smtp, body, from_addr, from_name, to_addr, subject, msgid=None, inreplyto=None):
    s = smtplib.SMTP(smtp)
    msg = MIMEText(body, _charset="utf-8")
    readable_from = email.header.Header(charset='utf8', header_name='From')
    readable_from.append(from_name)
    readable_from.append('<%s>' % (from_addr), charset='us-ascii')
    msg['From'] = readable_from
    msg['To'] = ",".join(to_addr)
    msg['Subject'] = Header(subject, 'utf-8')
    if msgid:
        msg['Message-ID'] = msgid
    if inreplyto:
        msg['In-Reply-To'] = inreplyto

    # from http://wordeology.com/computer/how-to-send-good-unicode-email-with-python.html
    m = StringIO()
    g = Generator(m, False)
    g.flatten(msg)
    s.sendmail(from_addr, to_addr, m.getvalue())
    sentMail = {"to":to_addr, "subject": subject}

    s.quit()
    return sentMail

if __name__ == "__main__":
    config = json.loads(io.open('instance/config.json').read())
    validate_repos(config)
    if os.environ.has_key('SCRIPT_NAME'):
        print serveRequest(config, sys.stdin.read())

