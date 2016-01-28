index.py is a python CGI script that provides a Webhook to be used as a github hook endpoint to send mail to a set of email addresses when specific events (e.g. push, new issues, etc) happen in specific repos.

It can also be used as a [W3C hook](https://w3c.github.io/w3c-api/webhooks) endpoint to send mail when TR documents get published.

The set of mailing lists, repos / TR documents and events is configured in a JSON file, named `mls.json` that lives in the same directory as the webhook, with the following structure:
```json
{
 "email@example.com": {
   "githubaccount/repo": {
      "events": ["issues.opened", "issues.closed", "issue_comment.created", "pull_request.opened", "pull_request.labeled"],
      "eventFilter": {"label":"important"}
      "branches: {
        "master": ["push"]
      }
   }
  },
 "email2@example.com": {
   "http://www.w3.org/TR/wake-lock": {
       "events": ["tr.published"]
    }
  }
}
```

In other words:
* each email address to which notifications are to be sent is a top level object
* in email objects, each repos / TR draft from which events need to be notified is an object
* in repo objects, there are 3 potential fields:
  * `events` is an array of Github events applicable to the repo as a whole; only events in that array will be notified
  * `eventFilter` is an optional set of filters that are applied to the events above; at the moment, only a `label` filter is defined, which means that only events that are associated with the said label will be notified
  * `branches` allows to describe events that are applicable at the branch level rather than the whole repo (e.g. "push")
* TR draft objects only take an `events` field, with `"tr.published"` currently the only supported event.

Only events for which templates have been defined (in the `templates/generic` directory) will be notified. Each mail target can have customized templates by creating an `email@example.com` directory in `templates/mls` and having a file named after the event. Templates use Mustache-based pystache as their engines and are fed with payload data from the event. The first line of the template is used as the subject of the email.

In addition to configuring targets of notifications, an instance of this webhook needs to define a `config.json` file with the SMTP host, the address from which messages will be sent, and set a GitHub OAUTH token that can be used to retrieve information via the GitHub API.

## W3C instance
W3C operates an instance of this service for WGs (and some CGs) repositories; if you want to make use of this service, please send pull requests on <a href="https://github.com/w3c/github-notify-ml-config">w3c/github-notify-ml-config</a> with amendments to the <code>mls.json</code> file for the mailing list(s) and repo(s) you’re interested in.

If you want to use a different text in the notifications, you can also provide pull requests that bring special per mailing list templates as described above.

## Testing
Run the test suite with:
```sh
python test_webhook.py
```

A typical test consists of:
* a JSON file with the payload of the github event / w3c event to be tested
* a .msg file that contains the email (with headers) expected to be sent by the webhook
* a new method in `test_webhook.py` `SendEmailGithubTests` that binds the event name, with the JSON file, and the email message