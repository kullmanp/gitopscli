import logging
import os
import uuid

from gitopscli.git.create_git import create_git
from gitopscli.io.yaml_util import update_yaml_file, yaml_dump
from gitopscli.io.tmp_dir import create_tmp_dir, delete_tmp_dir
from gitopscli.gitops_exception import GitOpsException


def deploy_command(
    command,
    file,
    values,
    username,
    password,
    git_user,
    git_email,
    create_pr,
    auto_merge,
    single_commit,
    organisation,
    repository_name,
    git_provider,
    git_provider_url,
    commit_message=None,
):
    assert command == "deploy"

    tmp_dir = create_tmp_dir()

    try:
        git = create_git(
            username,
            password,
            git_user,
            git_email,
            organisation,
            repository_name,
            git_provider,
            git_provider_url,
            tmp_dir,
        )
        git.checkout("master")
        logging.info("Master checkout successful")

        config_branch = f"gitopscli-deploy-{str(uuid.uuid4())[:8]}" if create_pr else "master"

        if create_pr:
            git.new_branch(config_branch)
            logging.info("Created branch %s", config_branch)

        updated_values = __update_values(git, file, values, single_commit, commit_message)
        if not updated_values:
            logging.info("All values already up-to-date. I'm done here")
            return

        git.push(config_branch)
        logging.info("Pushed branch %s", config_branch)
    finally:
        delete_tmp_dir(tmp_dir)

    if create_pr:
        __create_pr(git, config_branch, file, updated_values, auto_merge)


def __update_values(git, file, values, single_commit, commit_message):
    full_file_path = git.get_full_file_path(file)
    if not os.path.isfile(full_file_path):
        raise GitOpsException(f"No such file: {file}")

    updated_values = {}
    for key in values:
        value = values[key]
        try:
            updated_value = update_yaml_file(full_file_path, key, value)
        except KeyError as ex:
            raise GitOpsException(f"Key '{key}' not found in {file}") from ex
        if not updated_value:
            logging.info("Yaml property %s already up-to-date", key)
            continue
        logging.info("Updated yaml property %s to %s", key, value)
        updated_values[key] = value

        if not single_commit and commit_message is None:
            git.commit(f"changed '{key}' to '{value}' in {file}")

    if updated_values and single_commit and commit_message is None:
        if len(updated_values) == 1:
            key, value = list(updated_values.items())[0]
            git.commit(f"changed '{key}' to '{value}' in {file}")
        else:
            msg = f"updated {len(updated_values)} value{'s' if len(updated_values) > 1 else ''} in {file}"
            msg += f"\n\n{yaml_dump(updated_values)}"
            git.commit(msg)

    if updated_values and commit_message is not None:
        git.commit(commit_message)

    return updated_values


def __create_pr(git, branch, file, updated_values, auto_merge):
    title = f"Updated values in {file}"
    description = f"""\
Updated {len(updated_values)} value{'s' if len(updated_values) > 1 else ''} in `{file}`:
```yaml
{yaml_dump(updated_values)}
```
"""
    pull_request = git.create_pull_request(branch, "master", title, description)
    logging.info("Pull request created: %s", git.get_pull_request_url(pull_request))

    if auto_merge:
        git.merge_pull_request(pull_request)
        logging.info("Pull request merged")

        git.delete_branch(branch)
        logging.info("Branch '%s' deleted", branch)
