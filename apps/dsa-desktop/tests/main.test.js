const assert = require('node:assert/strict');
const test = require('node:test');
const Module = require('node:module');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function loadMainModule(t, options = {}) {
  const originalLoad = Module._load;
  const originalPlatform = Object.getOwnPropertyDescriptor(process, 'platform');
  const fakeApp = {
    isPackaged: false,
    getVersion: () => '3.12.0',
    getPath: () => '/tmp/dsa-user-data',
    whenReady: () => ({ then: () => undefined }),
    on: () => undefined,
    quit: () => undefined,
    ...(options.app || {}),
  };
  const fakeDialog = {
    showMessageBox: async () => ({ response: 0 }),
  };
  const fakeShell = {
    openExternal: async () => true,
  };
  const fakeIpcMain = {
    handle: () => undefined,
  };
  const fakeBrowserWindow = {
    getAllWindows: () => [],
  };
  const fakeNativeTheme = {
    shouldUseDarkColors: false,
    on: () => undefined,
    removeListener: () => undefined,
  };

  Module._load = function patchedLoad(request, parent, isMain) {
    if (request === 'electron') {
      return {
        app: fakeApp,
        BrowserWindow: fakeBrowserWindow,
        dialog: fakeDialog,
        ipcMain: fakeIpcMain,
        shell: fakeShell,
        nativeTheme: fakeNativeTheme,
      };
    }
    return originalLoad.call(this, request, parent, isMain);
  };

  const mainPath = require.resolve('../main.js');
  delete require.cache[mainPath];

  t.after(() => {
    Module._load = originalLoad;
    if (options.platform && originalPlatform) {
      Object.defineProperty(process, 'platform', originalPlatform);
    }
    delete require.cache[mainPath];
  });

  if (options.platform) {
    Object.defineProperty(process, 'platform', { ...originalPlatform, value: options.platform });
  }

  return require('../main.js');
}

test('parseSemver accepts stable and prerelease tags', (t) => {
  const mainModule = loadMainModule(t);

  assert.deepEqual(mainModule.parseSemver('v3.13.0-beta.2'), {
    major: 3,
    minor: 13,
    patch: 0,
    prerelease: ['beta', '2'],
  });
  assert.equal(mainModule.parseSemver('nightly-20260425'), null);
});

test('compareVersions follows semantic version ordering', (t) => {
  const mainModule = loadMainModule(t);

  assert.equal(mainModule.compareVersions('3.12.0', '3.13.0'), -1);
  assert.equal(mainModule.compareVersions('v3.13.0', '3.13.0'), 0);
  assert.equal(mainModule.compareVersions('3.13.0', '3.13.0-beta.1'), 1);
  assert.equal(mainModule.compareVersions('3.13.0-beta.2', '3.13.0-beta.10'), -1);
});

test('extractReleaseMetadata ignores releases without semver tags', (t) => {
  const mainModule = loadMainModule(t);

  assert.equal(
    mainModule.extractReleaseMetadata({
      tag_name: 'desktop-latest',
      html_url: 'https://github.com/ZhuLinsen/daily_stock_analysis/releases/tag/desktop-latest',
    }),
    null
  );
});

test('evaluateReleaseUpdate reports update-available when release is newer', (t) => {
  const mainModule = loadMainModule(t);
  const state = mainModule.evaluateReleaseUpdate({
    currentVersion: '3.12.0',
    release: {
      tag_name: 'v3.13.0',
      html_url: 'https://github.com/ZhuLinsen/daily_stock_analysis/releases/tag/v3.13.0',
      published_at: '2026-04-25T01:00:00Z',
      name: 'v3.13.0',
    },
    checkedAt: '2026-04-25T01:02:00Z',
  });

  assert.equal(state.status, mainModule.UPDATE_STATUS.UPDATE_AVAILABLE);
  assert.equal(state.currentVersion, '3.12.0');
  assert.equal(state.latestVersion, '3.13.0');
  assert.equal(state.releaseUrl, 'https://github.com/ZhuLinsen/daily_stock_analysis/releases/tag/v3.13.0');
  assert.equal(state.checkedAt, '2026-04-25T01:02:00Z');
  assert.equal(state.publishedAt, '2026-04-25T01:00:00Z');
  assert.match(state.message, /发现新版本 3\.13\.0/);
});

test('evaluateReleaseUpdate reports up-to-date when version is current', (t) => {
  const mainModule = loadMainModule(t);
  const state = mainModule.evaluateReleaseUpdate({
    currentVersion: '3.13.0',
    release: {
      tag_name: 'v3.13.0',
      html_url: 'https://github.com/ZhuLinsen/daily_stock_analysis/releases/tag/v3.13.0',
    },
    checkedAt: '2026-04-25T01:02:00Z',
  });

  assert.equal(state.status, mainModule.UPDATE_STATUS.UP_TO_DATE);
  assert.equal(state.latestVersion, '3.13.0');
  assert.equal(state.releaseUrl, 'https://github.com/ZhuLinsen/daily_stock_analysis/releases/tag/v3.13.0');
  assert.equal(state.checkedAt, '2026-04-25T01:02:00Z');
  assert.equal(state.publishedAt, '');
});

test('evaluateReleaseUpdate reports error when current version is invalid', (t) => {
  const mainModule = loadMainModule(t);
  const state = mainModule.evaluateReleaseUpdate({
    currentVersion: 'build-20260425',
    release: {
      tag_name: 'v3.13.0',
      html_url: 'https://github.com/ZhuLinsen/daily_stock_analysis/releases/tag/v3.13.0',
    },
    checkedAt: '2026-04-25T01:02:00Z',
  });

  assert.equal(state.status, mainModule.UPDATE_STATUS.ERROR);
  assert.match(state.message, /不是有效的语义化版本/);
});

test('checkForDesktopUpdates delegates to release fetcher', async (t) => {
  const mainModule = loadMainModule(t);
  const state = await mainModule.checkForDesktopUpdates({
    currentVersion: '3.12.0',
    fetchLatestRelease: async () => ({
      tag_name: 'v3.13.0',
      html_url: '',
    }),
  });

  assert.equal(state.status, mainModule.UPDATE_STATUS.UPDATE_AVAILABLE);
  assert.equal(state.releaseUrl, mainModule.RELEASES_PAGE_URL);
});

test('sanitizeReleaseUrl falls back for non-release links', (t) => {
  const mainModule = loadMainModule(t);

  assert.equal(
    mainModule.sanitizeReleaseUrl('https://example.com/not-allowed'),
    mainModule.RELEASES_PAGE_URL
  );
  assert.equal(
    mainModule.sanitizeReleaseUrl(
      `https://github.com/${mainModule.GITHUB_OWNER}/${mainModule.GITHUB_REPO}/releases/tag/v3.13.0`
    ),
    `https://github.com/${mainModule.GITHUB_OWNER}/${mainModule.GITHUB_REPO}/releases/tag/v3.13.0`
  );
});

test('fetchLatestReleaseJson rejects when response stream errors', async (t) => {
  const mainModule = loadMainModule(t);
  const response = new EventEmitter();
  response.statusCode = 200;
  response.complete = false;
  let destroyed = false;

  const request = () => {
    const req = new EventEmitter();
    req.destroyed = false;
    req.setTimeout = () => undefined;
    req.destroy = () => {
      destroyed = true;
      req.destroyed = true;
    };
    req.end = () => {
      process.nextTick(() => {
        request.onResponse(response);
        response.emit('error', new Error('stream failed'));
      });
    };
    return req;
  };
  request.onResponse = () => undefined;

  const pending = mainModule.fetchLatestReleaseJson({
    request: (_url, _options, onResponse) => {
      request.onResponse = onResponse;
      return request();
    },
  });

  await assert.rejects(pending, /stream failed/);
  assert.equal(destroyed, true);
});

test('desktop update backup list includes WAL and SHM artifacts', (t) => {
  const mainModule = loadMainModule(t);
  const files = mainModule.DESKTOP_UPDATE_RUNTIME_RELATIVE_FILES || [];
  assert.equal(Array.isArray(files), true);
  assert.ok(files.includes(path.join('data', 'stock_analysis.db')));
  assert.ok(files.includes(path.join('data', 'stock_analysis.db-wal')));
  assert.ok(files.includes(path.join('data', 'stock_analysis.db-shm')));
  assert.ok(files.includes(path.join('logs', 'desktop.log')));
});

test('restorePackagedRuntimeStateFromBackup keeps backup when copy fails', (t) => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'dsa-desktop-restore-'));
  const appDir = path.join(tempRoot, 'app');
  const userDataDir = path.join(tempRoot, 'userData');
  const backupRoot = path.join(userDataDir, '.dsa-desktop-update-backup');
  const backupDbPath = path.join(backupRoot, 'data', 'stock_analysis.db');
  fs.mkdirSync(path.dirname(backupDbPath), { recursive: true });
  fs.mkdirSync(appDir, { recursive: true });
  fs.writeFileSync(path.join(appDir, 'Uninstall Daily Stock Analysis.exe'), '');
  fs.writeFileSync(backupDbPath, 'backup-db');
  fs.writeFileSync(
    path.join(backupRoot, 'runtime-state.json'),
    JSON.stringify({ files: [path.join('data', 'stock_analysis.db')] }),
    'utf-8'
  );

  const mainModule = loadMainModule(t, {
    platform: 'win32',
    app: {
      isPackaged: true,
      getPath: (name) => {
        if (name === 'exe') {
          return path.join(appDir, 'Daily Stock Analysis.exe');
        }
        return userDataDir;
      },
    },
  });
  const originalCopyFileSync = fs.copyFileSync;
  let failedCopyAttempted = false;

  fs.copyFileSync = (source, target) => {
    if (source === backupDbPath) {
      failedCopyAttempted = true;
      throw new Error('target locked');
    }
    return originalCopyFileSync(source, target);
  };

  t.after(() => {
    fs.copyFileSync = originalCopyFileSync;
    fs.rmSync(tempRoot, { recursive: true, force: true });
  });

  const restoreResult = mainModule.restorePackagedRuntimeStateFromBackup();
  assert.equal(failedCopyAttempted, true);
  assert.equal(Array.isArray(restoreResult.failed), true);
  assert.equal(restoreResult.failed.length > 0, true);
  assert.equal(fs.existsSync(backupRoot), true);
  assert.equal(fs.existsSync(path.join(backupRoot, 'runtime-state.json')), true);
  assert.equal(restoreResult.failed[0].includes('target locked'), true);
});

test('stopBackend waits for backend process exit', async (t) => {
  const mainModule = loadMainModule(t);
  const killSignals = [];
  const fakeBackend = new EventEmitter();

  fakeBackend.pid = 4321;
  fakeBackend.killed = false;
  fakeBackend.exitCode = null;
  fakeBackend.signalCode = null;
  fakeBackend.kill = (signal) => {
    killSignals.push(signal);
    fakeBackend.killed = true;
    if (signal === 'SIGTERM' || signal === 'SIGKILL') {
      process.nextTick(() => {
        fakeBackend.exitCode = 0;
        fakeBackend.emit('exit', 0, null);
      });
    }
  };

  mainModule.__setBackendProcessForTest(fakeBackend);

  t.after(() => {
    mainModule.__setBackendProcessForTest(null);
  });

  await Promise.race([
    mainModule.stopBackend(),
    new Promise((_, reject) => setTimeout(() => reject(new Error('stopBackend did not resolve')), 200)),
  ]);

  assert.equal(killSignals.includes('SIGTERM'), true);
});
