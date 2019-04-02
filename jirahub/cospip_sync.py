import os
import sys
import argparse
import datetime
from getpass import getpass

import logging
logging.basicConfig(level=logging.INFO)

from githubquery import GithubQuery
from jiraquery import JiraQuery
from jirahub import how_issues_differ, IssueSync

__all__ = ['COS_Sync']

#-------------------------------------------------------------------------------

def cos_pipeline_cosbot(issues):

    # sync the issues between the two projects

    for l in issues:
        jid, gid = l.split()
        sync = COS_Sync(g, j, gid, jid)
        sync.status()
        # I only want to sync the github comments to JIRA b/c of the nature of internal-only comments
        sync.comments()

        for username in ['rplesha', 'efrazer']:
            all_watchers = [watcher.name for watcher in j.jira.watchers(jid).watchers]
            if username not in all_watchers:
                j.jira.add_watcher(jid, username)
                print('Adding {} to watch:'.format(username), jid)

    return

#-------------------------------------------------------------------------------

class COS_Sync(IssueSync):

    def comments(self):
        if 'comments' not in self.differences:
           return

        # get all the comments
        github_comments = self.github.issue.get_comments()
        jira_comments = self.jira.issue.fields.comment.comments
        github_comments_body = [g.body.strip() for g in self.github.issue.get_comments()]
        jira_comments_body = [j.body.strip() for j in self.jira.issue.fields.comment.comments]

        for g in github_comments:
            if g.body not in jira_comments_body:
                # we don't want to add the same comment over and over again
                #   b/c we are only syncing it one way
                self.jira.add_comment(f'{g.body}')

    def status(self):
        if 'status' not in self.differences:
            return

        if self.differences['status']:
            github_status = self.differences['status'][0]
            jira_status = self.differences['status'][1]

            # If the github status is closed, move the jira issue to resolved
            if github_status == 'closed':
                 if jira_status not in ['Done', 'Documentation', 'Ready for Delivery']:
                     logging.info('moving {} to Done'.format(self.jira_id))
                     self.jira.change_status('Done')

            # If the jira issue is resolved or done, close the github issue
            if jira_status in ['Done', 'Documentation', 'Ready for Delivery']:
                 if github_status is not 'closed':
                      self.github.change_status('closed')

            # if the jira issue is being tested, github should have a label to
            #   reflect that it's being developed:
            if jira_status in ['Selected for Development', 'Implementation',
                               'In Testing', 'Pending Merge to Test', 'Validation']:
                 self.github.change_labels([jira_status])

#-------------------------------------------------------------------------------

class lock:

    def __init__(self, lockfile):
       self.lockfile = lockfile

    def __enter__(self):
       fout = open(self.lockfile, 'w')
       fout.write(str(datetime.datetime.now))
       fout.close()

    def __exit__(self):
       os.remove(self.lockfile)

#-------------------------------------------------------------------------------

def jira_to_github(rosetta_stone, gitrepo, g, j):

    issues = open(rosetta_stone).readlines()

    # Add any new issues determine the issues that are open
    jira_issues = [x.split()[0] for x in issues]
    jira_search = 'Project="COSPIP" AND labels="CalCOS" AND \
                  (status != "OPEN" AND status != "BACKLOG" AND status != "DONE")'

    for i in j.jira.search_issues(jira_search):
        # check those issues against the list
        if i.key not in jira_issues:
            j.issue = i.key

            # The description has too much information in it. The summary should be enough
            description = j.issue.fields.summary
            body = f'Issue [{i.key}]({j.issue.permalink()}) was created by \
                     {j.issue.fields.creator}:\n\n{description}'

            # if they are not in the list, create an issue in github,
            gid = g.repo.create_issue(j.issue.fields.summary, body=body)
            print(gid)
            # add to list and then write it back out
            with open(rosetta_stone, 'a') as fout:
                fout.write(f'{i.key} {gid.number}\n')

            # add a comment to the JIRA project with a link back
            j.add_comment(f'This ticket is now being tracked on GitHub at \
                           [#{gid.number}|https://github.com/{gitrepo}/issues/{gid.number}]')

            # add the jira label to the github issue
            g.issue = gid.number
            g.issue.add_to_labels('jira')
            # add a github label to the jira issue
            added_labels = [issue.fields.labels.append(label) for label in [u'github']]
            j.issue.update(fields={"labels": issue.fields.labels})

#-------------------------------------------------------------------------------

def github_to_jira(rosetta_stone, gitrepo, g, j):

    issues = open(rosetta_stone).readlines()

    # Add any new issues determine the issues that are open
    # github issue numbers are ints but issues is read in as a string
    github_issues = [int(x.split()[1]) for x in issues]
    for i in g.repo.get_issues(state="open"):
        # check those issues against the list
        if i.number not in github_issues:
            print(f'{i.number}: "{i.title}" not in JIRA')
            # The description has too much information in it. The summary should be enough
            summary = i.title
            if i.body != None:
                body = i.body
            else:
                body = summary

            # if they are not in the list, create an issue in JIRA
            issue_dict = {'project': j.repo,
                          'summary': summary,
                          'description': body,
                          'issuetype': {'name': 'Software'},
                          }
            jid = j.jira.create_issue(fields=issue_dict)
            print(jid)
            # add to list and then write it back out
            with open(rosetta_stone, 'a') as fout:
                fout.write(f'{jid.key} {i.number}\n')

            # add the jira label to the github issue
            i.add_to_labels('jira')
            # add a github label to the jira issue
            added_labels = [jid.fields.labels.append(label) for label in [u'github', u'CalCOS']]
            jid.update(fields={"labels": jid.fields.labels})

#-------------------------------------------------------------------------------

if __name__=='__main__':

    # These are all defined in my envfile (export KEYWORD=value) and source envfile
    gituser = os.environ['GITUSER']
    gitkey = os.environ['GITKEY']
    gitrepo = os.environ['GITREPO']

    jirauser = os.environ['JIRAUSER']
    jirapass = os.environ['JIRAPASS']
    #jirapass = getpass() # I don't want to enter my password in a file
    jirarepo = os.environ['JIRAREPO']

    lockfile = 'cos_sync.lock'
    if os.path.isfile(lockfile):
       logging.info('COS_Sync is already running')
       sys.exit(-1)

    g = GithubQuery(gitrepo, gitkey)
    j = JiraQuery(jirarepo, user=jirauser, password=jirapass)

    parser = argparse.ArgumentParser(description='Sync a jira project and github repository')
    parser.add_argument('issue_list', type=str,
                        help='List of GitHub and JIRA Issue translations. Format: JIRA# GitHub#')
    args = parser.parse_args()

    # creating issues on github that are unique on jira and writing them out to issue_list
    jira_to_github(args.issue_list, gitrepo, g, j)

    # creating issues on jira that are unique on github and writing them out to issue_list
    github_to_jira(args.issue_list, gitrepo, g, j)

    # process for syning issues
    # reading the file again to open all files, including the ones that were just
    #   added in `jira_to_github` and `github_to_jira`
    #   Therefore this line needs to be run last!!
    cos_pipeline_cosbot(issues = open(args.issue_list).readlines())
