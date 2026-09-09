"""Frozen SWE v4.1 specifications and an offline, unprivileged official grader."""

import argparse
import dataclasses
import hashlib
import json
import runpy
import sys
from importlib.metadata import version
from pathlib import Path

import docker

REVISION = "726c5461e2ef52d83cf1ea2107870a8bb3328d57"


def key(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()


def prepare(dataset, cache):
    from swebench.harness.test_spec.test_spec import make_test_spec

    client = docker.from_env()
    cache.mkdir(parents=True, exist_ok=True)
    for row in json.loads(dataset.read_text()):
        spec = make_test_spec(row, namespace="swebench", arch="x86_64")
        try:
            image = client.images.get(spec.instance_image_key)
        except docker.errors.ImageNotFound:
            image = client.images.pull(spec.instance_image_key, platform="linux/amd64")
        payload = {"revision": REVISION, "spec": dataclasses.asdict(spec), "image": image.id}
        (cache / (key(row) + ".json")).write_text(json.dumps(payload))
        print(row["instance_id"], "environment frozen", flush=True)


def evaluate(cache):
    import swebench.harness.docker_build as build
    import swebench.harness.test_spec.test_spec as specs

    images = {}
    original_spec = specs.make_test_spec

    def frozen_spec(instance, *args, **kwargs):
        if isinstance(instance, specs.TestSpec):
            return instance
        payload = json.loads((cache / (key(instance) + ".json")).read_text())
        if payload["revision"] != REVISION:
            raise ValueError("SWE specification revision mismatch")
        images[instance["instance_id"]] = payload["image"]
        return specs.TestSpec(**payload["spec"])

    specs.make_test_spec = frozen_spec
    # Other official modules may already have imported the function.
    for module in list(sys.modules.values()):
        if (
            getattr(module, "__name__", "").startswith("swebench.")
            and getattr(module, "make_test_spec", None) is original_spec
        ):
            module.make_test_spec = frozen_spec

    def secure_container(test_spec, client, run_id, logger, nocache, force_rebuild=False):
        if force_rebuild:
            raise ValueError("Evaluation cannot rebuild environments")
        image = images[test_spec.instance_id]
        client.images.get(image)  # Never pull during grading.
        return client.containers.create(
            image=image,
            name=test_spec.get_instance_container_name(run_id),
            user="root",
            detach=True,
            command="tail -f /dev/null",
            platform="linux/amd64",
            network_mode="none",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            mem_limit="4g",
            nano_cpus=2_000_000_000,
            pids_limit=256,
        )

    build.build_container = secure_container
    runpy.run_module("swebench.harness.run_evaluation", run_name="__main__")


if __name__ == "__main__":
    if version("swebench") != "4.1.0":
        raise ValueError("Install the frozen SWE-bench 4.1.0 evaluator first")
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--prepare", type=Path)
    args, remaining = parser.parse_known_args()
    if args.prepare:
        prepare(args.prepare, args.cache)
    else:
        sys.argv = [sys.argv[0], *remaining]
        evaluate(args.cache)
