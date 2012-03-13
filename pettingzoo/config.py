import zc.zk
import zookeeper
import yaml
import random
import sys
import knewton.config

def connect_to_zk(servers):
	"""
	Function used to connect to zookeeper for pettingzoo.multiprocessing.
	Parameters:
		server - list of zookeeper servers to connect to in the form of a comma
			seperated list of addresses:port'localhost:2181,10.5.2.1:2181'
	Returns:
		zc.zk.ZooKeeper connection
	"""
	return zc.zk.ZooKeeper(servers)

CONFIG_PATH = "/config"

def _get_local_ip(interface='eth0'):
	"""
	Local helper function that returns the local ip address for a given interface.
	"""
	from netifaces import interfaces, ifaddresses, AF_INET
	addresses = {}
	if interface in interfaces():
		addresses = [i['addr'] for i in ifaddresses(interface).setdefault(AF_INET, [{'addr': None}] )]
		addresses = [addr for addr in addresses if addr]
		if len(addresses) > 0:
			return addresses[0]
		else:
			raise Exception("Interface %s does not have an ip address" % interface)
	else:
		raise Exception("Interface %s does not exist" % interface)

def _config_path_to_class_and_name(path):
	"""
	Local helper function that takes a knewton config path and returns it in the
	service_class/service_name format expected in this module
	"""
	path = path.split(".")[0]
	parts = path.split("/")
	if len(parts) >= 2:
		service_class = parts[-2]
		service_name = parts[-1]
		return service_class, service_name
	else:
		raise Exception("Config path cannot be parsed: %s" % path)

def _znode_to_class_and_name(znode):
	"""
	Local helper function that takes a full znode path that returns it in the
	service_class/service_name format expected in this module
	"""
	znode = znode.split("/")
	znode.pop(0)
	return (znode[0], znode[1])

def _znode_path(service_class, service_name, ip_address=None):
	"""
	Local helper function that creates the znode pathusing the service_class/service_name
	format that is utilized in this module
	"""
	znarr = [CONFIG_PATH, service_class, service_name]
	if ip_address:
		znarr.append(ip_address)
	return "/".join(znarr)

class DistributedConfig(object):
	"""
	DistributedConfig is a class that uses zookeeper to be able to manage the configs necessary for
	systems to interact with one another.  It has a fallback scheme that does the following:
	1) Try and find the config in zookeeper.
	1.1) If there is more the one config for that service, select one at random and return it.
	2) If there is no config in zookeeper, attempt to find one on the file system using the rules
		from knewton config.
	3) Error
	If any config changes in zookeeper for that service, a passed in callback will be executed, selecting
	a new node at random from the current nodes, allowing the user to reconfigure.
	Parameters:
		connection - a zc.zk.ZooKeeper connection
	Note:
		callbacks should be in the form of some_callback(path, config) where path will be passed in as
		the znode path to the service, and config is the config hash
	"""
	def __init__(self, connection):
		self.connection = connection
		self.connection.create_recursive(CONFIG_PATH, "", acl=zc.zk.OPEN_ACL_UNSAFE)
		self.cache = {}
		self.callbacks = {}
		self.children = {}

	def _get_config_from_cache(self, znode_path, callback=None):
		if callback:
			cset = self.callbacks.setdefault(znode_path, set())
			cset.add(callback)
		return self.cache.get(znode_path, None)

	def _store_config_in_cache(self, znode_path, config):
		self.cache[znode_path] = config
		
	def load_config(self, service_class, service_name, callback=None):
		"""
		Returns a config using the fallback scheme for the class to select
		a config at random from the available configs for a particular service.
		Parameters:
			service_class - the classification of the service (e.g. databases, memcached, etc)
			service_name - the name of the service (grover, knewmena, etc)
			callback - callback function to call if the config for this service changes. (Optional)
		Returns:
			the dict of the config in the standard knewton config format.
		"""
		path = _znode_path(service_class, service_name)
		cached = self._get_config_from_cache(path, callback)
		if cached:
			return cached
		config = self.__load_znodes(path)
		if config:
			return config
		config = self._load_file_config(service_class, service_name)
		self._store_config_in_cache(path, config)
		return config

	def __load_znodes(self, path, add_callback=True):
		if self.connection.exists(path):
			children = self.connection.children(path)
			if add_callback:
				children(self._child_callback)
				self.children[path] = children
			if len(children) > 0:
				selectee = random.choice([c for c in children])
				znode = path + "/" + selectee
				config = (selectee, yaml.load(self.connection.get(znode)[0]))
				self._store_config_in_cache(path, config)
				return config

	def load_config_via_path(self, path, callback=None):
		"""
		Returns a config using the fallback scheme for the DistributedConfig to select
		a config at random from the available configs for a particular service.
		Note, the passed in path will work with any path that is compatible with
		kenwton config.
		Parameters:
			path - a knewton config style config path (memcached/sessions, databases/knewmena.yml, etc)
			callback - callback function to call if the config for this service changes. (Optional)
		"""
		service_class, service_name = _config_path_to_class_and_name(path)
		return self.load_config(service_class, service_name, callback)

	def _load_file_config(self, service_class, service_name, callback=None):
		path = '/'.join([service_class, service_name])
		return knewton.config.fetch_knewton_config(path)

	def _child_callback(self, children):
		path = children.path
		service_class, service_name = _znode_to_class_and_name(path)
		config = self.__load_znodes(path, add_callback=False)
		callbacks = self.callbacks.get(path, [])
		for callback in callbacks:
			callback(path, config)

class DistributedMultiConfig(DistributedConfig):
	"""
	DistributedConfig is a class that uses zookeeper to be able to manage the configs necessary for
	systems to interact with one another.  It has a fallback scheme that does the following:
	1) Try and find the config in zookeeper.
	1.1) Return all configs for that service.
	2) If there is no config in zookeeper, attempt to find one on the file system using the rules
		from knewton config.
	3) Error
	If any config changes in zookeeper for that service, a passed in callback will be executed, returning
	all configs for the service, allowing the user to reconfigure.
	Parameters:
		connection - a zc.zk.ZooKeeper connection
	Note:
		callbacks should be in the form of some_callback(path, config) where path will be passed in as
		the znode path to the service, and config is the config hash
	"""
	def load_config(self, service_class, service_name, callback=None):
		"""
		Returns a config using the fallback scheme for DistributedConfig to select
		a config at random from the available configs for a particular service.
		Parameters:
			service_class - the classification of the service (e.g. databases, memcached, etc)
			service_name - the name of the service (grover, knewmena, etc)
			callback - callback function to call if the config for this service changes. (Optional)
		Returns:
			an array of tuples.  the tuples contain the ip address, the config dict.
			the dict of the config in the standard knewton config format.
			if the system has to fall back to files, the ip address will instead be the string 'file'
		"""
		path = _znode_path(service_class, service_name)
		cached = self._get_config_from_cache(path, callback)
		if cached:
			return cached
		config = self.__load_znodes(path)
		if config:
			return config
		config = [('file', self._load_file_config(service_class, service_name))]
		self._store_config_in_cache(path, config)
		return config

	def __load_znodes(self, path, add_callback=True):
		if self.connection.exists(path):
			children = self.connection.children(path)
			if add_callback:
				children(self._child_callback)
				self.children[path] = children
			if len(children) > 0:
				config = []
				for child in children:
					znodep = path + "/" + child
					znode = self.connection.get(znodep)
					single = yaml.load(znode[0])
					config.append((child, single))
				self._store_config_in_cache(path, config)
				return config

def write_distributed_config(connection, service_class, service_name, config, ip_address=None, interface='eth0', ephemeral=True):
	if not ip_address:
		ip_address = _get_local_ip(interface)
	path = _znode_path(service_class, service_name)
	connection.create_recursive(path, "", acl=zc.zk.OPEN_ACL_UNSAFE)
	payload = yaml.dump(config)
	flags = 0
	if ephemeral:
		flags = zookeeper.EPHEMERAL
	znode = _znode_path(service_class, service_name, ip_address)
	if connection.exists(znode):
		connection.delete(znode)
	connection.create(znode, payload, zc.zk.OPEN_ACL_UNSAFE, flags)

def remove_stale_config(connection, service_class, service_name, ip_address):
	connection.delete(_znode_path(service_class, service_name, ip_address))

