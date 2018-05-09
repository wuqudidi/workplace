# -*- coding: utf-8 -*-
import inspect,asyncio,logging,os,functools
#inspect模块获取类或函数的参数的信息
from aiohttp import web
from urllib import parse
from apis import APIError

def get(path):
	def decorator(func):
		@functools.wraps(func)
		def wrapper(*args,**kw):
			return func(*args,**kw)
		wrapper.__method__='GET'
		wrapper.__route__=path
		return wrapper
	return decorator

def post(path):
	def decorator(func):
		@functools.wraps(func)
		def wrapper(*args,**kw):
			return func(*args,**kw)
		wrapper.__method__='POST'
		wrapper.__route__=path
		return wrapper
	return decorator

def has_request_arg(fn):
	#sig接受fn函数的所有参数
	sig = inspect.signature(fn)
	#params接受所有参数与其parameters属性的映射
	params = sig.parameters
	found = False
	for name,param in params.items():
		if name == 'request':
			found = True
			continue
	#kind为某一参数的Parameter.kind属性值，一般为下面几种类型
		if found and (param.kind != inspect.Parameter.VAR_POSITIONAL and param.kind!=inspect.Parameter.KEYWORD_ONLY and param.kind != inspect.Parameter.VAR_KEYWORD):
			raise ValueError('request parameter must be the last named parameter in function: %s%s' % (fn.__name__,str(sig)))
	return found
def has_var_kw_arg(fn):
	params = inspect.signature(fn).parameters
	for name,param in params.items():
		if param.kind == inspect.Parameter.VAR_KEYWORD:
			return True

def has_named_kw_args(fn):
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            return True

#获取关键字参数，形如**kw，没有限制的dict
def get_named_kw_args(fn):
    args = []
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            args.append(name)
    return tuple(args)


#获取没设默认值的命名关键字参数,用*，*分割后的参数
#指定了参数的名字，传入值组成dict{xx：xx}
#在最后的参数是request，以命名关键字参数形式存在
def get_required_kw_args(fn):
    args = []
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.KEYWORD_ONLY and param.default == inspect.Parameter.empty:
            args.append(name)
    return tuple(args)
#处理URl的函数，添加__call__可以作为函数使用即RequestHandler（）
#接收request的参数
#先拿到各种参数，用fn处理后返回fn这个函数
class RequestHandler(object):
	def __init__(self,app,fn):
		self._app=app
		self._func=fn
		self._has_request_arg=has_request_arg(fn)#检查有没有request参数
		self._has_var_kw_arg=has_var_kw_arg(fn)#检查有没有关键字参数（一组dict）
		self._has_named_kw_args=has_named_kw_args(fn)#检查有没有命名关键字（限制的dict）
		self._named_kw_args = get_named_kw_args(fn)
		self._required_kw_args=get_required_kw_args(fn)

	async def __call__(self,request):
		kw=None
		#如果有以上检查到的参数，判断其方法，将相应的数据放入字典kw中
		if self._has_var_kw_arg or self._has_named_kw_args or self._required_kw_args:
			if request.method == 'POST':
				if not request.content_type:
					return web.HTTPBadRequest('Missing Content-Type.')
				ct = request.content_type.lower()
				#startswith检查是否以指定字符串开头
				if ct.startswith('application/json'):
					#返回body中的json字符串
					params = await request.json()
					if not isinstance(params,dict):
						return web.HTTPBadRequest('JSON body must be object.')
					kw = params
				elif ct.startswith('application/x-www-form-urlencoded') or ct.startswith('multipart/form-data'):
					params = await request.post()
					kw = dict(**params)
				else:
					return web.HTTPBadRequest('Unsupported Content-Type: %s' % request.content_type)
			if request.method == 'GET':
				#得到url中？后所有的值
				qs = request.query_string
				if qs:
					kw = dict()
					#parse_qs函数返回一组字典
					for k,v in parse.parse_qs(qs,True).items():
						kw[k]=v[0]
		#此时kw还为空，即没有json对象和post传入的参数，get的？后也没有参数
		if kw is None:
			#request.match_info获取request的各项参数
			kw=dict(**request.match_info)
		else:
			#如果有非json、post、get形式传入的参数，拿到它（命名时指定的）存入字典kw
			if not self._has_var_kw_arg and self._named_kw_args:
				#移除所有未命名参数
				copy = dict()
				for name in self._named_kw_args:
					if name in kw:
						copy[name] = kw[name]
				kw = copy
			for k,v in request.match_info.items():
				if  k in kw:
					logging.warning('Duplicate arg name in named arg and kw args: %s' % k)
					kw[k]=v
		if self._has_request_arg:
			kw['request']=request
			#如果有拿到命名关键字参数，但是kw没有，即post等几种形式未装入到此种参数
		if self._required_kw_args:
			for name in self._required_kw_args:
				if not name in kw:
					return web.HTTPBadRequest('Missing argument: %s' % name)
		logging.info('call with args: %s' % str(kw))
		#最后异步返回url处理函数与字典kw装入的使用post、json、get、match_info拿到的request的各种值
		try:
			r= await self._func(**kw)
			return r
		except APIError as e:
			return dict(error=e.error,data=e.data,message=e.message)


def add_route(app,fn):
	method= getattr(fn,'__method__',None)
	path = getattr(fn,'__route__',None)
	if path is None or method is None:
		raise ValueError('@get or @post not defined in %s.' % str(fn))
	if not asyncio.iscoroutinefunction(fn) and not inspect.isgeneratorfunction(fn):
		fn = asyncio.coroutine(fn)
	logging.info('add route %s %s => %s(%s)' % (method,path,fn.__name__, ','.join(inspect.signature(fn).parameters.keys())))
	app.router.add_route(method,path,RequestHandler(app,fn))

def add_routes(app,module_name):
	n = module_name.rfind('.')
	#动态加载module_name模块下的函数，name代表函数名
	if n==(-1):
		mod = __import__(module_name,globals(),locals())
	else:
		name=module_name[n+1:]
		mod = getattr(__import__(module_name[:n],globals(),locals(),[name]),name)
	#循环把该模块的所有函数（有method和path的）注册了
	#dir（）返回mod的所有属性和方法
	for attr in dir(mod):
		if attr.startswith('_'):
			continue
		fn = getattr(mod,attr)
		#callable（）检查对象是否可以调用，函数，方法，类与实现__call__的都返回True
		if callable(fn):
			method = getattr(fn,'__method__',None)
			path = getattr(fn,'__route__',None)
			if method and path:
				add_route(app,fn)
#给aiohttp添加静态资源的方法
def add_static(app):
	path = os.path.join(os.path.dirname(os.path.abspath(__file__)),'static')
	app.router.add_static('/static/',path)
	logging.info('add static %s => %s' % ('/static/',path))