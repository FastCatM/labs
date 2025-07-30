a=int(input().replace('-',''))
b=str(a)
c=b[::-1]
if int(c)==int(b):
    print('true')
else:
    print('false')