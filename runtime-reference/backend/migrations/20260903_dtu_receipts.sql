CREATE TABLE IF NOT EXISTS dtu_receipts (
    robot_id varchar(100) NOT NULL,
    msg_id varchar(100) NOT NULL,
    digest varchar(64) NOT NULL,
    kind varchar(30) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (robot_id, msg_id)
);
