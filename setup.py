#!/usr/bin/env python

from setuptools import setup, find_packages


setup(name='twitch-utils',
      version='1.0',
      author='Dmitry Karikh',
      author_email='the.dr.hax@gmail.com',
      license='GPLv3',
      url='https://github.com/TheDrHax/Twitch-Utils',
      packages=find_packages(),
      entry_points='''
        [console_scripts]
        twitch_utils=twitch_utils:main
      ''',
      )
