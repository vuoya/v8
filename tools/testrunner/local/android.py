# Copyright 2018 the V8 project authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
Wrapper around the Android device abstraction from src/build/android.
"""

import logging
import os
import sys


BASE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..'))
ANDROID_DIR = os.path.join(BASE_DIR, 'build', 'android')
DEVICE_DIR = '/data/local/tmp/v8/'


class TimeoutException(Exception):
  def __init__(self, timeout):
    self.timeout = timeout


class CommandFailedException(Exception):
  def __init__(self, status, output):
    self.status = status
    self.output = output


class _Driver(object):
  """Helper class to execute shell commands on an Android device."""
  def __init__(self, device=None):
    assert os.path.exists(ANDROID_DIR)
    sys.path.insert(0, ANDROID_DIR)

    # We import the dependencies only on demand, so that this file can be
    # imported unconditionally.
    import devil_chromium
    from devil.android import device_errors  # pylint: disable=import-error
    from devil.android import device_utils  # pylint: disable=import-error
    from devil.android.perf import cache_control  # pylint: disable=import-error
    from devil.android.perf import perf_control  # pylint: disable=import-error
    from devil.android.sdk import adb_wrapper  # pylint: disable=import-error
    global cache_control
    global device_errors
    global perf_control

    devil_chromium.Initialize()

    if not device:
      # Detect attached device if not specified.
      devices = adb_wrapper.AdbWrapper.Devices()
      assert devices, 'No devices detected'
      assert len(devices) == 1, 'Multiple devices detected.'
      device = str(devices[0])
    self.adb_wrapper = adb_wrapper.AdbWrapper(device)
    self.device = device_utils.DeviceUtils(self.adb_wrapper)

    # This remembers what we have already pushed to the device.
    self.pushed = set()

  def tear_down(self):
    """Clean up files after running all tests."""
    self.device.RemovePath(DEVICE_DIR, force=True, recursive=True)

  def push_file(self, host_dir, file_name, target_rel='.',
                skip_if_missing=False):
    """Push a single file to the device (cached).

    Args:
      host_dir: Absolute parent directory of the file to push.
      file_name: Name of the file to push.
      target_rel: Parent directory of the target location on the device
          (relative to the device's base dir for testing).
      skip_if_missing: Keeps silent about missing files when set. Otherwise logs
          error.
    """
    file_on_host = os.path.join(host_dir, file_name)

    # Only push files not yet pushed in one execution.
    if file_on_host in self.pushed:
      return

    file_on_device_tmp = os.path.join(DEVICE_DIR, '_tmp_', file_name)
    file_on_device = os.path.join(DEVICE_DIR, target_rel, file_name)
    folder_on_device = os.path.dirname(file_on_device)

    # Only attempt to push files that exist.
    if not os.path.exists(file_on_host):
      if not skip_if_missing:
        logging.critical('Missing file on host: %s' % file_on_host)
      return

    # Work-around for 'text file busy' errors. Push the files to a temporary
    # location and then copy them with a shell command.
    output = self.adb_wrapper.Push(file_on_host, file_on_device_tmp)
    # Success looks like this: '3035 KB/s (12512056 bytes in 4.025s)'.
    # Errors look like this: 'failed to copy  ... '.
    if output and not re.search('^[0-9]', output.splitlines()[-1]):
      logging.critical('PUSH FAILED: ' + output)
    self.adb_wrapper.Shell('mkdir -p %s' % folder_on_device)
    self.adb_wrapper.Shell('cp %s %s' % (file_on_device_tmp, file_on_device))
    self.pushed.add(file_on_host)

  def push_executable(self, shell_dir, target_dir, binary):
    """Push files required to run a V8 executable.

    Args:
      shell_dir: Absolute parent directory of the executable on the host.
      target_dir: Parent directory of the executable on the device (relative to
          devices' base dir for testing).
      binary: Name of the binary to push.
    """
    self.push_file(shell_dir, binary, target_dir)

    # Push external startup data. Backwards compatible for revisions where
    # these files didn't exist. Or for bots that don't produce these files.
    self.push_file(
        shell_dir,
        'natives_blob.bin',
        target_dir,
        skip_if_missing=True,
    )
    self.push_file(
        shell_dir,
        'snapshot_blob.bin',
        target_dir,
        skip_if_missing=True,
    )
    self.push_file(
        shell_dir,
        'snapshot_blob_trusted.bin',
        target_dir,
        skip_if_missing=True,
    )
    self.push_file(
        shell_dir,
        'icudtl.dat',
        target_dir,
        skip_if_missing=True,
    )

  def run(self, target_dir, binary, args, env, rel_path, timeout):
    """Execute a command on the device's shell.

    Args:
      target_dir: Parent directory of the executable on the device (relative to
          devices' base dir for testing).
      binary: Name of the binary.
      args: List of arguments to pass to the binary.
      env: The environment variables with which the command should be run.
      rel_path: Relative path on device to use as CWD.
      timeout: Timeout in seconds.
    """
    binary_on_device = os.path.join(DEVICE_DIR, target_dir, binary)
    cmd = [binary_on_device] + args
    try:
      output = self.device.RunShellCommand(
          cmd,
          cwd=os.path.join(DEVICE_DIR, rel_path),
          check_return=True,
          env=env,
          timeout=timeout,
          retries=0,
      )
      return '\n'.join(output)
    except device_errors.AdbCommandFailedError as e:
      raise CommandFailedException(e.status, e.output)
    except device_errors.CommandTimeoutError:
      raise TimeoutException(timeout)

  def drop_ram_caches(self):
    """Drop ran caches on device."""
    cache = cache_control.CacheControl(self.device)
    cache.DropRamCaches()

  def set_high_perf_mode(self):
    """Set device into high performance mode."""
    perf = perf_control.PerfControl(self.device)
    perf.SetHighPerfMode()

  def set_default_perf_mode(self):
    """Set device into default performance mode."""
    perf = perf_control.PerfControl(self.device)
    perf.SetDefaultPerfMode()


_ANDROID_DRIVER = None
def android_driver(device=None):
  """Singleton access method to the driver class."""
  global _ANDROID_DRIVER
  if not _ANDROID_DRIVER:
    _ANDROID_DRIVER = _Driver(device)
  return _ANDROID_DRIVER
