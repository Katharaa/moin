#!/bin/sh

# Using a list of packages and associated jobs, define a configuration for apt
# that allows the listed packages to be obtained for installation.
#
# Usage: add-dependencies.sh <apt base directory> <package job list file>

THISDIR=$(dirname "$0")
APT_PATH=${1:-"/etc/apt"}
SALSA_JOBS_LIST=${2:-"$THISDIR/salsa-jobs.txt"}

# Define repository location details for package dependencies.

TEAM_URL="https://salsa.debian.org/moin-team"
APTLY_URL_SUFFIX="artifacts/raw/aptly"
PUBKEY_URL_SUFFIX="$APTLY_URL_SUFFIX/public-key.asc"

# Add dependency details to an apt configuration in the indicated directory.
#
# Usage: add_dependencies <apt base directory> <package job list file>

add_dependencies()
{
    APT_LIST="$1/new-packages-testing.list"
    APT_GPG_TRUSTED_DIR="$1/trusted.gpg.d"

    # Create the configuration resources.

    echo -n > "$APT_LIST"

    if [ ! -e "$APT_GPG_TRUSTED_DIR" ] ; then
        mkdir -p "$APT_GPG_TRUSTED_DIR"
    fi

    # Add configuration entries and public keys.

    while read PACKAGE JOBID ; do
        cat >> "$APT_LIST" <<EOF
deb ${TEAM_URL}/${PACKAGE}/-/jobs/${JOBID}/${APTLY_URL_SUFFIX} unstable main
EOF
        get_public_key "$APT_GPG_TRUSTED_DIR" "$PACKAGE" "$JOBID"
    done < "$2"
}

# Fetch the public key for a package, storing it in the indicated directory.
#
# Usage: get_public_key <directory> <package name> <job identifier>

get_public_key()
{
    wget -q -O "$1/$2.asc" ${TEAM_URL}/$2/-/jobs/$3/${PUBKEY_URL_SUFFIX}
}

# Obtain the package and job details, adding dependency details.

add_dependencies "$APT_PATH" "$SALSA_JOBS_LIST"
