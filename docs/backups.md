# Backups and restore

How to keep daily, encrypted backups of the app's database off your computer, and how to
check that they can really be restored. All commands are Windows PowerShell, run in the
project folder with the virtual environment active.

## How it works

- `backup --to-folder` writes a dated, encrypted copy of the database into a folder you
  choose, checks it, and keeps only the newest 14.
- If that folder is inside OneDrive (or another synced folder), the copies leave your
  computer automatically. The app itself never uploads anything.
- Scheduled backups can't ask for a passphrase, so they are locked with a **key file**
  instead. The key file must stay **outside** the backup folder: anyone with both the
  backups and the key can read the data.
- `backup drill` restores the newest backup into a temporary copy and checks it against
  the live database, without changing the live database.
- The owner's dashboard shows the last good backup and the last passed drill.

## 1. Make the key file (once)

```powershell
python -m opsapp backup make-key --key "$HOME\Documents\opsapp-backup.key"
```

Keep a second copy of the key file somewhere safe that is not the backup folder, for
example a USB stick in a drawer or a password manager. **Without the key, the backups
can't be opened by anyone, including you.**

## 2. Make a first backup by hand

```powershell
python -m opsapp backup --to-folder "$HOME\OneDrive\opsapp-backups" --key "$HOME\Documents\opsapp-backup.key"
```

Use your own OneDrive folder name if it is different, for example
`"$HOME\OneDrive - Your Company\opsapp-backups"`.

## 3. Run it every day with Windows Task Scheduler

Run these once, in the project folder. They create a task that runs the backup every day
at 6 pm, or at the next start-up if the computer was off. It runs only while you are
signed in to Windows.

```powershell
$project = (Get-Location).Path
$python  = "$project\.venv\Scripts\python.exe"
$folder  = "$HOME\OneDrive\opsapp-backups"
$key     = "$HOME\Documents\opsapp-backup.key"
$action  = New-ScheduledTaskAction -Execute $python -Argument "-m opsapp backup --to-folder `"$folder`" --key `"$key`" --keep 14" -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Daily -At 6pm
$options = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName "opsapp daily backup" -Action $action -Trigger $trigger -Settings $options -Description "Encrypted backup of the opsapp database" -Force

# Try it now and check the result (LastTaskResult 0 means it worked)
Start-ScheduledTask -TaskName "opsapp daily backup"
Start-Sleep -Seconds 20
Get-ScheduledTaskInfo -TaskName "opsapp daily backup" | Select-Object LastRunTime, LastTaskResult
Get-Content data\backup.log -Tail 3
```

**If you move to a new project folder** (for example a new zip), the task still points at
the old folder. Run the same commands again from the new folder; `-Force` replaces the
task. The database lives in the project's `data` folder unless `OPSAPP_DATABASE_PATH` in
`.env` says otherwise.

To remove the task: `Unregister-ScheduledTask -TaskName "opsapp daily backup" -Confirm:$false`.

## 4. Restore drill (once a month)

```powershell
python -m opsapp backup drill --folder "$HOME\OneDrive\opsapp-backups" --key "$HOME\Documents\opsapp-backup.key"
```

It prints a report and saves it in `data\drills\`. A passed drill shows on the dashboard
for 31 days.

The drill fails if the backup has more records than the live database, for example if
a company has disappeared since the backup. If you deleted a company on purpose with
`company delete`, make a new backup and run the drill again.

## Restoring for real

Stop the server and dispatcher first. Make a backup of the current state too, in case you
need to go back.

```powershell
python -m opsapp restore --from "$HOME\OneDrive\opsapp-backups\opsapp-20261006-180000.opsbak" --key "$HOME\Documents\opsapp-backup.key" --yes
python -m opsapp db upgrade
```

## Older passphrase backups

Backups made with `backup --out FILE --encrypt` (0.4.0) still work as before: they ask for
their passphrase and don't use a key file.
