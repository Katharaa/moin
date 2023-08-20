#!/bin/sh

# Using a list of packages and associated jobs, define a configuration for apt
# that allows the listed packages to be obtained for installation.
#
# Usage: add-dependencies.sh <apt base directory>

APT_PATH=${1:-"/etc/apt"}

# The jobs list would preferably be separate, but this requires extra effort
# when setting up various environments.

SALSA_JOBS_LIST='
emeraldtree 4575438
feedgen 4575509
flask-theme 4575496
flatland 4576442
python-xstatic-autosize 4576472
python-xstatic-bootstrap 4576577
python-xstatic-ckeditor 4576527
python-xstatic-jquery-file-upload 4576540
python-xstatic-pygments 4576563
'

# Define repository location details for package dependencies.

TEAM_URL="https://salsa.debian.org/moin-team"
APTLY_URL_SUFFIX="artifacts/raw/aptly"
PUBKEY_URL_SUFFIX="$APTLY_URL_SUFFIX/public-key.asc"

# Add dependency details to an apt configuration in the indicated directory.
#
# Usage: add_dependencies <apt base directory>

add_dependencies()
{
    APT_LIST="$1/new-packages-testing.list"
    APT_GPG_TRUSTED_DIR="$1/trusted.gpg.d"

    # Create the configuration resources.

    echo -n > "$APT_LIST"

    if [ ! -e "$APT_GPG_TRUSTED_DIR" ] ; then
        mkdir -p "$APT_GPG_TRUSTED_DIR"
    fi

    # Extract the package and job identifier from the list.

    echo "$SALSA_JOBS_LIST" | while read PACKAGE JOBID ; do
        if [ ! "$PACKAGE" ] ; then
            continue
        fi

        # Add configuration entries and public keys.

        cat >> "$APT_LIST" <<EOF
deb ${TEAM_URL}/${PACKAGE}/-/jobs/${JOBID}/${APTLY_URL_SUFFIX} unstable main
EOF
        get_public_key "$APT_GPG_TRUSTED_DIR" "$PACKAGE" "$JOBID"
    done
}

# Fetch the public key for a package, storing it in the indicated directory.
#
# Usage: get_public_key <directory> <package name> <job identifier>

get_public_key()
{
    wget -q -O "$1/$2.asc" ${TEAM_URL}/$2/-/jobs/$3/${PUBKEY_URL_SUFFIX}
}

# First, make sure that wget is available.

apt-get update
NON_INTERACTIVE=1 apt-get install -y wget

# Obtain the package and job details, adding dependency details.

add_dependencies "$APT_PATH"
