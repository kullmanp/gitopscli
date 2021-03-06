import hashlib
import logging
import os
import shutil

from gitopscli.git.create_git import create_git
from gitopscli.io.gitops_config import GitOpsConfig
from gitopscli.io.yaml_util import update_yaml_file
from gitopscli.io.tmp_dir import create_tmp_dir, delete_tmp_dir
from gitopscli.gitops_exception import GitOpsException


def create_preview_command(
    command,
    username,
    password,
    git_user,
    git_email,
    organisation,
    repository_name,
    git_provider,
    git_provider_url,
    git_hash,
    preview_id,
    deployment_already_up_to_date_callback=None,
    deployment_exists_callback=None,
    deployment_new_callback=None,
):

    assert command is not None

    apps_tmp_dir = create_tmp_dir()
    root_tmp_dir = create_tmp_dir()

    try:
        apps_git = create_git(
            username,
            password,
            git_user,
            git_email,
            organisation,
            repository_name,
            git_provider,
            git_provider_url,
            apps_tmp_dir,
        )

        apps_git.checkout(git_hash)
        logging.info("App repo git hash %s checkout successful", git_hash)
        try:
            gitops_config = GitOpsConfig(apps_git.get_full_file_path(".gitops.config.yaml"))
        except FileNotFoundError as ex:
            raise GitOpsException(f"Couldn't find .gitops.config.yaml") from ex
        logging.info("Read .gitops.config.yaml: %s", gitops_config)

        root_git = create_git(
            username,
            password,
            git_user,
            git_email,
            gitops_config.team_config_org,
            gitops_config.team_config_repo,
            git_provider,
            git_provider_url,
            root_tmp_dir,
        )
        root_git.checkout("master")
        logging.info("Config repo branch master checkout successful")

        preview_template_folder_name = ".preview-templates/" + gitops_config.application_name
        if os.path.isdir(root_git.get_full_file_path(preview_template_folder_name)):
            logging.info("Using the preview template folder: %s", preview_template_folder_name)
        else:
            raise GitOpsException(f"The preview template folder does not exist: {preview_template_folder_name}")

        hashed_preview_id = hashlib.sha256(preview_id.encode("utf-8")).hexdigest()[:8]
        new_preview_folder_name = gitops_config.application_name + "-" + hashed_preview_id + "-preview"
        logging.info("New folder for preview: %s", new_preview_folder_name)
        preview_env_already_exist = os.path.isdir(root_git.get_full_file_path(new_preview_folder_name))
        logging.info("Is preview env already existing? %s", preview_env_already_exist)
        if not preview_env_already_exist:
            __create_new_preview_env(
                git_hash,
                new_preview_folder_name,
                preview_template_folder_name,
                root_git,
                gitops_config.application_name,
            )
        logging.info("Using image tag from git hash: %s", git_hash)
        route_host = None
        value_replaced = False
        for replacement in gitops_config.replacements:
            route_host, value_replaced = __replace_value(
                gitops_config,
                git_hash,
                new_preview_folder_name,
                replacement,
                root_git,
                route_host,
                hashed_preview_id,
                value_replaced,
            )
        if not value_replaced:
            logging.info("The image tag %s has already been deployed. Doing nothing.", git_hash)
            if deployment_already_up_to_date_callback:
                deployment_already_up_to_date_callback(apps_git, git_hash)
            return

        root_git.commit(f"Update preview environment for '{gitops_config.application_name}' and git hash '{git_hash}'.")
        root_git.push("master")
        logging.info("Pushed branch master")

        if preview_env_already_exist:
            if deployment_exists_callback:
                deployment_exists_callback(apps_git, gitops_config, route_host)
        else:
            if deployment_new_callback:
                deployment_new_callback(apps_git, gitops_config, route_host)
    finally:
        delete_tmp_dir(apps_tmp_dir)
        delete_tmp_dir(root_tmp_dir)


def __replace_value(
    gitops_config,
    new_image_tag,
    new_preview_folder_name,
    replacement,
    root_git,
    route_host,
    hashed_preview_id,
    value_replaced,
):
    replacement_value = None
    logging.info("Replacement: %s", replacement)
    replacement_path = replacement["path"]
    replacement_variable = replacement["variable"]
    if replacement_variable == "GIT_COMMIT":
        replacement_value = new_image_tag
    elif replacement_variable == "ROUTE_HOST":
        route_host = gitops_config.route_host.replace("{SHA256_8CHAR_BRANCH_HASH}", hashed_preview_id)
        logging.info("Created route host: %s", route_host)
        replacement_value = route_host
    else:
        logging.info("Unknown replacement variable: %s", replacement_variable)
    try:
        value_replaced = value_replaced | update_yaml_file(
            root_git.get_full_file_path(new_preview_folder_name + "/values.yaml"), replacement_path, replacement_value,
        )
    except KeyError as ex:
        raise GitOpsException(f"Key '{replacement_path}' not found in '{new_preview_folder_name}/values.yaml'") from ex
    logging.info("Replacing property %s with value: %s", replacement_path, replacement_value)
    return route_host, value_replaced


def __create_new_preview_env(
    git_hash, new_preview_folder_name, preview_template_folder_name, root_git, app_name,
):
    shutil.copytree(
        root_git.get_full_file_path(preview_template_folder_name), root_git.get_full_file_path(new_preview_folder_name),
    )
    chart_file_path = new_preview_folder_name + "/Chart.yaml"
    logging.info("Looking for Chart.yaml at: %s", chart_file_path)
    if root_git.get_full_file_path(chart_file_path):
        try:
            update_yaml_file(root_git.get_full_file_path(chart_file_path), "name", new_preview_folder_name)
        except KeyError as ex:
            raise GitOpsException(f"Key 'name' not found in '{chart_file_path}'") from ex
    root_git.commit(f"Create new preview environment for '{app_name}' and git hash '{git_hash}'.")
