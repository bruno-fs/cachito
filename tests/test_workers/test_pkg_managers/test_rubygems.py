# SPDX-License-Identifier: GPL-3.0-or-later
import logging
from pathlib import Path
from textwrap import dedent
from unittest import mock

import pytest
import requests

from cachito.errors import NexusError, ValidationError
from cachito.workers.errors import NexusScriptError
from cachito.workers.pkg_managers import general, rubygems
from cachito.workers.pkg_managers.rubygems import GemMetadata, parse_gemlock

GIT_REF = "26487618a68443e94d623bb585cb464b07d36702"


def setup_module():
    """Re-enable logging that was disabled at some point in previous tests."""
    rubygems.log.disabled = False
    rubygems.log.setLevel(logging.DEBUG)
    general.log.disabled = False
    general.log.setLevel(logging.DEBUG)


class TestNexus:
    """Nexus related tests."""

    @mock.patch("cachito.workers.pkg_managers.rubygems.nexus.execute_script")
    def test_prepare_nexus_for_rubygems_request(self, mock_exec_script):
        """Check whether groovy script is called with proper args."""
        rubygems.prepare_nexus_for_rubygems_request(
            "cachito-rubygems-hosted-1", "cachito-rubygems-raw-1"
        )

        mock_exec_script.assert_called_once_with(
            "rubygems_before_content_staged",
            {
                "rubygems_repository_name": "cachito-rubygems-hosted-1",
                "raw_repository_name": "cachito-rubygems-raw-1",
            },
        )

    @mock.patch("cachito.workers.pkg_managers.rubygems.nexus.execute_script")
    def test_prepare_nexus_for_rubygems_request_failed(self, mock_exec_script):
        """Check whether proper error is raised on groovy script failures."""
        mock_exec_script.side_effect = NexusScriptError()

        expected = "Failed to prepare Nexus for Cachito to stage Rubygems content"
        with pytest.raises(NexusError, match=expected):
            rubygems.prepare_nexus_for_rubygems_request(
                "cachito-rubygems-hosted-1", "cachito-rubygems-raw-1"
            )


class TestGemlockParsing:
    @pytest.mark.parametrize(
        "file_contents, expected_dependencies",
        (
            # GEM dependency
            (
                dedent(
                    """
                    GEM
                      remote: https://rubygems.org/
                      specs:
                        zeitwerk (2.5.4)

                    PLATFORMS
                      ruby

                    DEPENDENCIES
                      zeitwerk
                    """
                ),
                [GemMetadata("zeitwerk", "2.5.4", "GEM", "https://rubygems.org/")],
            ),
            # GIT dependency
            (
                dedent(
                    """
                    GIT
                      remote: https://github.com/3scale/ci_reporter_shell.git
                      revision: 30b30d655512891f56463e5f1fa125ea1f2df886
                      specs:
                        ci_reporter_shell (0.1.0)
                          ci_reporter (~> 2.0)

                    GEM
                      remote: https://rubygems.org/
                      specs:
                        builder (3.2.4)
                        ci_reporter (2.0.0)
                          builder (>= 2.1.2)

                    PLATFORMS
                      ruby

                    DEPENDENCIES
                      ci_reporter_shell!
                    """
                ),
                [
                    GemMetadata(
                        "ci_reporter_shell",
                        "30b30d655512891f56463e5f1fa125ea1f2df886",
                        "GIT",
                        "https://github.com/3scale/ci_reporter_shell.git",
                    ),
                    GemMetadata("ci_reporter", "2.0.0", "GEM", "https://rubygems.org/"),
                    GemMetadata("builder", "3.2.4", "GEM", "https://rubygems.org/"),
                ],
            ),
            # GEM dependencies without specified version should be skipped
            (
                dedent(
                    """
                    GEM
                      remote: https://rubygems.org/
                      specs:
                        zeitwerk

                    PLATFORMS
                      ruby

                    DEPENDENCIES
                      zeitwerk
                    """
                ),
                [],
            ),
        ),
    )
    def test_parsing_of_valid_cases(self, file_contents, expected_dependencies, tmpdir):
        gemfile_lock = tmpdir.join("Gemfile.lock")
        gemfile_lock.write(file_contents)

        dependencies = parse_gemlock(Path(gemfile_lock).parent, Path(gemfile_lock))

        assert len(dependencies) == len(expected_dependencies)
        for dep, expected_dep in zip(dependencies, expected_dependencies):
            assert expected_dep == dep

    def test_gemlock_not_a_file(self, tmpdir):
        gemfile_lock = tmpdir.join("Gemfile.lock")

        with pytest.raises(ValidationError) as exc_info:
            parse_gemlock(Path(gemfile_lock).parent, Path(gemfile_lock))

        expected_msg = (
            f"Gemfile.lock at path {gemfile_lock} does not exist or is not a regular file."
        )
        assert expected_msg == str(exc_info.value)

    def test_parsing_of_valid_path_dependency(self, tmpdir):
        gemlock_contents = dedent(
            """
            PATH
              remote: local/zeitwerk
              specs:
                zeitwerk (2.6.0)

            GEM
              remote: https://rubygems.org/
              specs:

            PLATFORMS
              ruby

            DEPENDENCIES
              zeitwerk!
            """
        )
        expected_dependencies = [GemMetadata("zeitwerk", "2.6.0", "PATH", "local/zeitwerk")]

        gemfile_lock = tmpdir.join("Gemfile.lock")
        gemfile_lock.write(gemlock_contents)
        tmpdir.mkdir("local").mkdir("zeitwerk")

        dependencies = parse_gemlock(Path(gemfile_lock).parent, Path(gemfile_lock))

        assert len(dependencies) == len(expected_dependencies)
        for dep, expected_dep in zip(dependencies, expected_dependencies):
            assert expected_dep == dep

    @pytest.mark.parametrize(
        "file_contents, expected_error",
        (
            (
                dedent(
                    """
                    GEM
                      remote: https://rubygems.org/
                      specs:
                        zeitwerk (2.5.4)

                    DEPENDENCIES
                      zeitwerk
                    """
                ),
                "PLATFORMS section of Gemfile.lock has to contain one and only platform - ruby.",
            ),
            (
                dedent(
                    """
                    GEM
                      remote: http://rubygems.org/
                      specs:
                        zeitwerk (2.5.4)

                    PLATFORMS
                      ruby

                    DEPENDENCIES
                      zeitwerk
                    """
                ),
                "Cachito supports only https://rubygems.org/ as a remote for Ruby GEM "
                "dependencies.",
            ),
            (
                dedent(
                    """
                    GIT
                      remote: http://github.com/3scale/json-schema.git
                      revision: 26487618a68443e94d623bb585cb464b07d36702
                      specs:
                        json-schema (3.0.0)
                          addressable (>= 2.4)

                    PLATFORMS
                      ruby

                    DEPENDENCIES
                      json-schema!
                    """
                ),
                "All Ruby GIT dependencies have to use HTTPS protocol.",
            ),
            (
                dedent(
                    """
                    GIT
                      remote: https://github.com/3scale/json-schema.git
                      revision: xxx
                      specs:
                        json-schema (3.0.0)
                          addressable (>= 2.4)

                    PLATFORMS
                      ruby

                    DEPENDENCIES
                      json-schema!
                    """
                ),
                "No git ref for gem: json-schema (expected 40 hexadecimal characters, got: xxx).",
            ),
            (
                dedent(
                    """
                    UNSUPPORTED
                      remote: vendor/active-docs
                      specs:
                        active-docs (1.0.0)
                          railties (> 3.1)

                    PLATFORMS
                      ruby

                    DEPENDENCIES
                      active-docs!
                    """
                ),
                "Gemfile.lock contains unsupported dependency type.",
            ),
            (
                dedent(
                    """
                    GEM
                      remote: https://rubygems.org/
                      specs:
                        zeitwerk (2.5.4)

                    PLATFORMS
                      ruby
                      x86_64-linux

                    DEPENDENCIES
                      zeitwerk
                    """
                ),
                "PLATFORMS section of Gemfile.lock has to contain one and only platform - ruby.",
            ),
        ),
    )
    def test_parsing_of_invalid_cases(self, file_contents, expected_error, tmpdir):
        """Test the invalid use cases of dependencies in a Gemfile.lock file."""
        gemfile_lock = tmpdir.join("Gemfile.lock")
        gemfile_lock.write(file_contents)

        with pytest.raises(ValidationError) as exc_info:
            parse_gemlock(Path(gemfile_lock).parent, Path(gemfile_lock))

        assert expected_error == str(exc_info.value)

    @pytest.mark.parametrize(
        "file_contents, expected_error",
        (
            (
                dedent(
                    """
                    PATH
                      remote: vendor/active-docs
                      specs:
                        active-docs (1.0.0)

                    PLATFORMS
                      ruby

                    DEPENDENCIES
                      active-docs!
                    """
                ),
                "PATH dependency active-docs references a non-existing path: ",
            ),
        ),
    )
    def test_parsing_of_invalid_paths(self, file_contents, expected_error, tmpdir):
        """Test the invalid path definition in a Gemfile.lock file."""
        gemfile_lock = tmpdir.join("Gemfile.lock")
        gemfile_lock.write(file_contents)

        with pytest.raises(ValidationError) as exc_info:
            parse_gemlock(Path(gemfile_lock).parent, Path(gemfile_lock))

        assert expected_error in str(exc_info.value)

    def test_path_is_not_a_subpath_absolute(self, tmpdir):
        active_docs = Path(tmpdir).joinpath("active-docs")
        active_docs.mkdir()

        gemlock_contents = dedent(
            f"""
            PATH
              remote: {str(active_docs)}
              specs:
                active-docs (1.0.0)

            PLATFORMS
              ruby

            DEPENDENCIES
              active-docs!
            """
        )
        gemfile_lock = tmpdir.mkdir("gem").join("Gemfile.lock")
        gemfile_lock.write(gemlock_contents)
        gemfile_lock = Path(gemfile_lock)

        with pytest.raises(ValueError) as exc_info:
            parse_gemlock(gemfile_lock.parent, gemfile_lock)

        expected_msg = f"{str(active_docs)} is not a subpath of {str(gemfile_lock.parent)}"
        assert expected_msg in str(exc_info.value)

    def test_paths_is_not_a_subpath_relative_path(self, tmpdir):
        gemlock_contents = dedent(
            """
            PATH
              remote: ../active-docs
              specs:
                active-docs (1.0.0)

            PLATFORMS
              ruby

            DEPENDENCIES
              active-docs!
            """
        )
        gemfile_lock = tmpdir.mkdir("gem").join("Gemfile.lock")
        gemfile_lock.write(gemlock_contents)
        gemfile_lock = Path(gemfile_lock)

        active_docs = Path(tmpdir).joinpath("active-docs")
        active_docs.mkdir()

        with pytest.raises(ValueError) as exc_info:
            parse_gemlock(gemfile_lock.parent, gemfile_lock)

        expected_msg = f"{str(active_docs)} is not a subpath of {str(gemfile_lock.parent)}"
        assert expected_msg in str(exc_info.value)

    @mock.patch("secrets.token_hex")
    @mock.patch("cachito.workers.pkg_managers.rubygems.nexus.execute_script")
    def test_finalize_nexus_for_rubygems_request(self, mock_exec_script, mock_secret):
        """Check whether groovy script is called with proper args."""
        mock_secret.return_value = "password"
        password = rubygems.finalize_nexus_for_rubygems_request(
            "cachito-rubygems-hosted-1", "cachito-rubygems-raw-1", "user-1"
        )

        mock_exec_script.assert_called_once_with(
            "rubygems_after_content_staged",
            {
                "rubygems_repository_name": "cachito-rubygems-hosted-1",
                "raw_repository_name": "cachito-rubygems-raw-1",
                "username": "user-1",
                "password": "password",
            },
        )

        assert password == "password"

    @mock.patch("cachito.workers.pkg_managers.rubygems.nexus.execute_script")
    def test_finalize_nexus_for_rubygems_request_failed(self, mock_exec_script):
        """Check whether proper error is raised on groovy script failures."""
        mock_exec_script.side_effect = NexusScriptError()
        expected = "Failed to configure Nexus Rubygems repositories for final consumption"
        with pytest.raises(NexusError, match=expected):
            rubygems.finalize_nexus_for_rubygems_request(
                "cachito-rubygems-hosted-1", "cachito-rubygems-raw-1", "user-1"
            )


class MockBundleDir(type(Path())):
    """Mocked RequestBundleDir."""

    def __new__(cls, *args, **kwargs):
        """Make a new MockBundleDir."""
        self = super().__new__(cls, *args, **kwargs)
        self.rubygems_deps_dir = self / "deps" / "rubygems"
        return self


class TestDownload:
    """Tests for dependency downloading."""

    @pytest.mark.parametrize("have_raw_component", [True, False])
    @mock.patch("cachito.workers.pkg_managers.rubygems.download_binary_file")
    def test_download_rubygems_package(self, mock_download_file, have_raw_component, tmp_path):
        gem = GemMetadata("zeitwerk", "2.5.4", "GEM", "https://rubygems.org/")

        download_info = rubygems._download_rubygems_package(
            gem, tmp_path, "https://rubygems-proxy.org/", ("user", "password")
        )

        assert download_info == {
            "package": "zeitwerk",
            "version": "2.5.4",
            "path": tmp_path / "zeitwerk" / "zeitwerk-2.5.4.gem",
        }

        proxied_file_url = "https://rubygems-proxy.org/gems/zeitwerk-2.5.4.gem"
        mock_download_file.assert_called_once_with(
            proxied_file_url, download_info["path"], auth=("user", "password")
        )

    @pytest.mark.parametrize("have_raw_component", [True, False])
    @mock.patch("cachito.workers.pkg_managers.rubygems.nexus.get_raw_component_asset_url")
    @mock.patch("cachito.workers.pkg_managers.general.download_binary_file")
    @mock.patch("cachito.workers.pkg_managers.rubygems.Git")
    @mock.patch("shutil.copy")
    def test_download_git_package(
        self,
        mock_shutil_copy,
        mock_git,
        mock_download_file,
        mock_get_component_url,
        have_raw_component,
        tmp_path,
        caplog,
    ):
        raw_url = "https://nexus:8081/repository/cachito-rubygems-raw/json.tar.gz"

        dependency = GemMetadata("json", GIT_REF, "GIT", "https://github.com/org/json.git")

        git_archive_path = tmp_path / "json.tar.gz"

        mock_get_component_url.return_value = raw_url if have_raw_component else None
        mock_git.return_value = mock.Mock()
        mock_git.return_value.sources_dir.archive_path = git_archive_path

        download_info = rubygems._download_git_package(
            dependency, tmp_path, "cachito-rubygems-raw", ("username", "password")
        )

        raw_component = f"json/json-external-gitcommit-{GIT_REF}.tar.gz"

        assert download_info == {
            "package": "json",
            "path": tmp_path.joinpath(
                "github.com", "org", "json", f"json-external-gitcommit-{GIT_REF}.tar.gz"
            ),
            "url": "https://github.com/org/json.git",
            "ref": GIT_REF.lower(),
            "raw_component_name": raw_component,
            "have_raw_component": have_raw_component,
        }

        assert (
            f"Looking for raw component '{raw_component}' in 'cachito-rubygems-raw'" in caplog.text
        )

        if have_raw_component:
            assert f"Found raw component, will download from '{raw_url}'" in caplog.text
            mock_download_file.assert_called_once_with(
                raw_url, download_info["path"], auth=("username", "password")
            )
            mock_git.assert_not_called()
            mock_shutil_copy.assert_not_called()
        else:
            assert "Raw component not found, will fetch from git" in caplog.text
            mock_download_file.assert_not_called()
            mock_git.assert_called_once_with("https://github.com/org/json.git", GIT_REF)
            mock_git.return_value.fetch_source.assert_called_once_with(gitsubmodule=False)
            mock_shutil_copy.assert_called_once_with(git_archive_path, download_info["path"])

    @pytest.mark.parametrize("have_raw_component", [True, False])
    @mock.patch("cachito.workers.pkg_managers.rubygems.RequestBundleDir")
    @mock.patch("cachito.workers.pkg_managers.rubygems.get_worker_config")
    @mock.patch("cachito.workers.pkg_managers.rubygems.nexus.get_nexus_hoster_credentials")
    @mock.patch("cachito.workers.pkg_managers.rubygems._download_rubygems_package")
    @mock.patch("cachito.workers.pkg_managers.rubygems._download_git_package")
    @mock.patch("cachito.workers.pkg_managers.rubygems.upload_raw_package")
    def test_download_dependencies(
        self,
        mock_upload_raw,
        mock_git_download,
        mock_rubygems_download,
        mock_get_nexus_creds,
        mock_get_config,
        mock_request_bundle_dir,
        have_raw_component,
        tmp_path,
        caplog,
    ):
        """
        Test dependency downloading.

        Mock the helper functions used for downloading here, test them properly elsewhere.
        """
        # <setup>
        git_url = "https://github.com/baz/bar.git"

        rubygems_dep = GemMetadata("foo", "2.0.0", "GEM", "https://rubygems.org/")
        git_dep = GemMetadata("bar", GIT_REF, "GIT", git_url)
        dependencies = [rubygems_dep, git_dep]

        mock_bundle_dir = MockBundleDir(tmp_path)
        rubygems_deps_path = mock_bundle_dir.rubygems_deps_dir

        rubygems_download = rubygems_deps_path / "foo" / "foo-2.0.0.gem"
        git_archive_name = f"bar-external-gitcommit-{GIT_REF}.tar.gz"
        git_download = rubygems_deps_path.joinpath("github.com", "baz", "bar", git_archive_name)
        rubygems_info = {
            "package": "foo",
            "version": "2.0.0",
            "path": rubygems_download,
        }
        git_info = {
            "package": "bar",
            "path": git_download,
            "url": git_url,
            "ref": GIT_REF.lower(),
            "raw_component_name": f"bar/bar-external-gitcommit-{GIT_REF}.tar.gz",
            "have_raw_component": have_raw_component,
        }

        mock_request_bundle_dir.return_value = mock_bundle_dir

        proxy_url = "https://proxy-rubygems.org/"
        raw_repo = "cachito_rubygems_raw"
        mock_get_config.return_value = mock.Mock(
            cachito_nexus_rubygems_proxy_url=proxy_url,
            cachito_nexus_rubygems_raw_repo_name=raw_repo,
        )

        mock_get_nexus_creds.return_value = ("username", "password")
        nexus_auth = requests.auth.HTTPBasicAuth("username", "password")

        mock_rubygems_download.return_value = rubygems_info
        mock_git_download.return_value = git_info

        request_id = 1
        # </setup>

        # <exercise>
        downloads = rubygems.download_dependencies(request_id, dependencies)
        # </exercise>

        # <verify>
        assert downloads == [
            {**rubygems_info, "kind": "GEM"},
            {**git_info, "kind": "GIT"},
        ]
        assert rubygems_deps_path.is_dir()

        # <check calls that must always be made>
        mock_request_bundle_dir.assert_called_once_with(request_id)
        mock_get_config.assert_called_once()
        mock_get_nexus_creds.assert_called_once()
        mock_rubygems_download.assert_called_once_with(
            rubygems_dep, rubygems_deps_path, proxy_url, nexus_auth
        )
        mock_git_download.assert_called_once_with(git_dep, rubygems_deps_path, raw_repo, nexus_auth)
        # </check calls that must always be made>

        # <check calls to raw package upload method>
        if not have_raw_component:
            assert (
                f"Uploading '{git_archive_name}' to '{raw_repo}' as "
                f"'{git_info['raw_component_name']}'"
            ) in caplog.text
            mock_upload_raw.assert_any_call(
                raw_repo, git_download, git_dep.name, git_archive_name, is_request_repository=False
            )
        assert mock_upload_raw.call_count == (0 if have_raw_component else 1)
        # </check calls to raw package upload method>

        # <check basic logging output>
        assert f"Downloading {rubygems_dep.name} ({rubygems_dep.version})" in caplog.text
        assert (
            f"Successfully downloaded gem {rubygems_dep.name} ({rubygems_dep.version}) "
            f"to deps/rubygems/foo/foo-2.0.0.gem"
        ) in caplog.text

        assert f"Downloading {git_dep.name} ({git_dep.version})" in caplog.text
        assert (
            f"Successfully downloaded gem {git_dep.name} ({git_dep.version}) "
            f"to deps/rubygems/github.com/baz/bar/"
            f"bar-external-gitcommit-{GIT_REF}.tar.gz"
        ) in caplog.text
        # </check basic logging output>
        # </verify>
