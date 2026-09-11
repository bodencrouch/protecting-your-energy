# Nomad activation gate

Nomad is the preferred scheduler after the Tailscale fleet has three reachable server nodes with low-latency links, persistent local state, mTLS, and a working Raft quorum. Run I/O workloads as parameterized batch jobs on client nodes. Configure task restart mode `fail` and a reschedule policy so a failed allocation can move to another client.

Before activation, verify:

- three or five Nomad servers are healthy and `nomad operator raft list-peers` shows one leader and the expected voters;
- every worker has a distinct failure domain and enough disk headroom;
- staged input exists on at least two eligible workers or in independently replicated self-hosted object storage;
- jobs use stable idempotency keys and write results atomically;
- Nomad RPC, HTTP, and Serf traffic is restricted to Tailscale addresses and protected with mTLS;
- loss of any one server leaves quorum and loss of any one worker leaves an input replica elsewhere.

Do not use the existing single Nomad process as evidence of HA. Do not deploy k3s merely to schedule batch commands; its three-node etcd control plane and Kubernetes storage stack add no benefit for this workload while the same three-node quorum requirement remains.

For durable shared artifacts, run Garage outside Nomad on three storage nodes with replication factor 3 and consistent mode. That gives read and write quorum with one storage node unavailable. The dispatcher and workers remain stateless; Garage is the explicitly stateful, replicated system of record. Node-local inputs, outputs, and scratch remain disposable.

Primary references:

- https://developer.hashicorp.com/nomad/docs/deploy/production/reference-architecture
- https://developer.hashicorp.com/nomad/docs/deploy/production/requirements
- https://developer.hashicorp.com/nomad/docs/concepts/scheduling/schedulers
- https://developer.hashicorp.com/nomad/docs/job-declare/failure/restart
- https://docs.k3s.io/datastore/ha-embedded
- https://garagehq.deuxfleurs.fr/documentation/reference-manual/configuration/
- https://garagehq.deuxfleurs.fr/documentation/cookbook/real-world/
