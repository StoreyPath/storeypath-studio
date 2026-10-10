# Users, sharing and backups

Studio keeps who may use it in its database, with the projects: the accounts, who each
project is shared with, the sessions and an audit log. Everyone logs in, and sees only
what is shared with them.

- **Roles.** An *admin* adds users, sets their role, disables them or gives a new
  temporary password, sees and does everything, and may give a project another owner.
  An *engineer* creates projects and opens packages and project files as new ones,
  and owns what they make. A *user* sees only what is shared with them. An admin may
  let anyone download backups (`backup`) or change the item types (`catalogue`).
- **Sharing.** A project's owner shares it from its page (*Share…*): a person gets
  *view*, *edit* or *share* on the whole project, one of its buildings or one of its
  floors. A grant on the project covers its buildings and floors, ones added later
  too; on a building, its floors. Someone with *share* on a building shares within
  that building only. A person who may see only some floors sees those alone: on the
  project page, in Review (read only with *view*: no tools, a *View only* badge), in
  3D, and in the drawing (only their floor's part of a sheet).
- **One editor a floor at a time.** The first change a person makes to a floor takes it;
  everyone else on it sees who is editing, and their changes as they are saved, until
  they are done, leave the floor, or leave it alone for 15 minutes (an admin may take it
  over). *Undo* and *Redo* take back a person's own changes; *History* lists who changed
  what ([Many people at once](../studio/README.md#many-people-at-once)).
- **First start.** With no users, Studio makes the first admin: `admin`, password
  `admin` (or `STOREYPATH_ADMIN_PASSWORD` when that is set). Anyone changes their own
  password in the person menu; passwords have no rules: each person's to choose.
- **HTTPS.** `storeypath serve` speaks HTTPS: with a certificate it makes itself in
  `<data>/tls/` for the names it is reached by (browsers warn once; it prints the
  fingerprint), or the organization's (`--cert`, `--key`); `--http --secure-cookies
  --trusted-proxy <its address>` behind a proxy that does the HTTPS, which must pass
  who is asking in `X-Real-IP` (Studio refuses calls through it without). Plain
  `http://` to its port is redirected.
- **Command line**, on the data folder, also while Studio runs: `storeypath users add
  NAME --role admin|engineer|user [--capability backup]` (asks for the password
  twice; `--password-stdin` for scripts), `users list`, `users passwd`, `users disable`
  / `enable`, `users role`.
- **Backups.** *Download a backup* (admins, and whoever may) or `storeypath backup`
  gives Studio's whole database as one file (gzip'd SQL, as `psql` reads it,
  `storeypath-backup-<time>.sql.gz`): the projects with their drawings and
  exports, item types, the accounts with their passwords' hashes, sharing and the audit
  log (no session), so the `backup` capability hands those over too. Studio's
  certificate is not in it: a restored Studio makes a new one (browsers warn once).
  `storeypath restore` loads a backup into an empty Studio. The image holds `pg_dump`,
  `pg_restore` and `psql` (17) for doing it by hand.
- **The audit log**, on the Users page: logins (and failed and throttled ones), logouts,
  passwords changed and reset, users created, changed and disabled, grants, owners
  changed, projects created, opened and deleted, exports, backups and the setup: when,
  who, from what address.

Every setting, how sessions and failed logins are handled, and what each call of
Studio's API needs: [studio/README.md](../studio/README.md#users-sharing-and-backups).
