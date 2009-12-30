#!/bin/bash

#
# Install everything (python) into a virtual environment.
# Important in sandboxes to force Python 2.6.  Important
# locally to keep base installs clean.
#

echo '# installing base tools'
sudo easy_install-2.6 -U setuptools

echo
echo '# setting up a virtual environment'
sudo easy_install-2.6 -U virtualenv
virtualenv --no-site-packages sandbox-env
. sandbox-env/bin/activate

# from here on, everything is using Python2.6 from sandbox-env

echo
echo '# installing turbogears'
easy_install ipython
easy_install -UZ -i http://www.turbogears.org/2.1/downloads/current/index turbogears2==2.1a3
easy_install -UZ -i http://www.turbogears.org/2.1/downloads/current/index tg.devtools==2.1a3
easy_install beautifulsoup
easy_install mercurial

#
# Install _our_ code.
#
# echo
# echo '# cloning forge repo'
# git clone ssh://gitosis@engr.geek.net/forge
# cd forge
#
# This already happened just to get this file to run;
# now assume we run it in-place.

echo '# installing Ming and dependencies'
pushd ..
git clone git://merciless.git.sourceforge.net/gitroot/merciless/merciless Ming
cd Ming
python setup.py develop
popd

echo
echo '# creating data directory for mongo'
mkdir -p /data/db


#
# Install all our (formal) dependencies.
#

echo
echo '# installing pyforge dependencies'
pushd pyforge
python setup.py develop
popd

echo
echo '# installing ForgeSCM dependencies'
pushd ForgeSCM
python setup.py develop
popd

echo
echo '# installing ForgeWiki dependencies'
pushd ForgeWiki
python setup.py develop
popd

echo
echo '# installing HelloForge dependencies'
pushd HelloForge
python setup.py develop
popd
