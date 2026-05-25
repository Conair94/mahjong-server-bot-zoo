CREATE TABLE accounts (
            account_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            username          TEXT NOT NULL UNIQUE
                                  CHECK (length(username) BETWEEN 3 AND 32),
            display_name      TEXT NOT NULL
                                  CHECK (length(display_name) BETWEEN 1 AND 64),
            kind              TEXT NOT NULL CHECK (kind IN ('human', 'bot')),
            role              TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
            password_hash     TEXT NOT NULL,
            disabled          INTEGER NOT NULL DEFAULT 0 CHECK (disabled IN (0, 1)),
            created_at_ms     INTEGER NOT NULL,
            last_login_ms     INTEGER
        )
CREATE TABLE hand_index (
            hand_id                 TEXT PRIMARY KEY,
            match_id                TEXT,
            hand_index_in_match     INTEGER NOT NULL DEFAULT 0,
            ruleset_id              TEXT NOT NULL,
            ruleset_config_hash     TEXT NOT NULL,
            started_at_ms           INTEGER NOT NULL,
            ended_at_ms             INTEGER,
            terminal_kind           TEXT CHECK (terminal_kind IN ('HU', 'EXHAUSTIVE_DRAW', 'ABORTED', NULL)),
            winner_seat             INTEGER CHECK (winner_seat BETWEEN 0 AND 3),
            fan_total               INTEGER,
            master_seed             TEXT NOT NULL,
            record_path             TEXT NOT NULL UNIQUE,
            record_checksum         TEXT NOT NULL,
            server_version          TEXT NOT NULL,
            source                  TEXT NOT NULL DEFAULT 'live'
                                        CHECK (source IN ('live', 'selfplay', 'replay-import'))
        )
CREATE TABLE hand_participants (
            hand_id              TEXT NOT NULL REFERENCES hand_index(hand_id) ON DELETE CASCADE,
            seat                 INTEGER NOT NULL CHECK (seat BETWEEN 0 AND 3),
            account_id           INTEGER REFERENCES accounts(account_id) ON DELETE SET NULL,
            seat_kind            TEXT NOT NULL CHECK (seat_kind IN ('human', 'bot', 'canned')),
            wind                 TEXT NOT NULL CHECK (wind IN ('F1', 'F2', 'F3', 'F4')),
            final_score_delta    INTEGER,
            PRIMARY KEY (hand_id, seat)
        )
CREATE TABLE schema_version (
            version           INTEGER NOT NULL PRIMARY KEY CHECK (version >= 0),
            applied_at_ms     INTEGER NOT NULL,
            applied_by        TEXT NOT NULL
        )
CREATE TABLE sessions (
            session_id        TEXT PRIMARY KEY,
            account_id        INTEGER NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
            issued_at_ms      INTEGER NOT NULL,
            expires_at_ms     INTEGER NOT NULL,
            last_seen_ms      INTEGER NOT NULL,
            revoked           INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0, 1)),
            user_agent        TEXT
        )
CREATE INDEX accounts_username_lower
            ON accounts(lower(username))
CREATE INDEX hand_index_match
            ON hand_index(match_id, hand_index_in_match)
CREATE INDEX hand_index_started
            ON hand_index(started_at_ms DESC)
CREATE INDEX hand_index_winner
            ON hand_index(winner_seat) WHERE winner_seat IS NOT NULL
CREATE INDEX hand_participants_account
            ON hand_participants(account_id, hand_id)
CREATE INDEX sessions_account_active
            ON sessions(account_id, revoked, expires_at_ms)
CREATE INDEX sessions_expires
            ON sessions(expires_at_ms)
